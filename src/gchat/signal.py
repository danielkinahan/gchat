from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import MessageSeed, NameChangeSeed, PersonSeed, SourceSeed, ChannelSeed
from .util import to_utc_naive


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


def _extract_attachment_count(chat_item: dict) -> int:
    standard = chat_item.get("standardMessage") or {}
    attachments = standard.get("attachments")
    if isinstance(attachments, list):
        return len(attachments)
    if "stickerMessage" in chat_item or "viewOnceMessage" in chat_item:
        return 1
    return 0


def _message_id(chat_item: dict) -> str:
    payload = json.dumps(chat_item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def normalize_backup(path: Path) -> SignalExport:
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
                group_titles[chat_id] = str(title.get("title") or f"Signal group {chat_id}")

    group_chat_ids = {chat_id for chat_id, chat in chats.items() if str(chat.get("recipientId") or "") in group_recipient_ids}

    relevant_recipient_ids: set[str] = set()
    relevant_stable_ids: set[str] = set()
    channels: dict[str, ChannelSeed] = {}
    name_changes: list[NameChangeSeed] = []

    for chat_id in group_chat_ids:
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
        if chat_id not in group_chat_ids:
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
                            ts=to_utc_naive(datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)),
                            kind="nickname-change",
                            payload_json=json.dumps(item, ensure_ascii=False),
                        )
                    )
            group_change = update.get("groupChange") or {}
            for change in group_change.get("updates") or []:
                if not isinstance(change, dict) or "groupNameUpdate" not in change:
                    continue
                title_update = change["groupNameUpdate"] or {}
                new_title = str(title_update.get("newGroupName") or "").strip()
                if not new_title:
                    continue
                current = channels[chat_id].name
                if current != new_title:
                    name_changes.append(
                        NameChangeSeed(
                            source_name=source.name,
                            platform="signal",
                            entity_kind="channel",
                            entity_raw_id=chat_id,
                            previous_name=current,
                            new_name=new_title,
                            ts=to_utc_naive(datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)),
                            kind="channel-title-change",
                            payload_json=json.dumps(item, ensure_ascii=False),
                        )
                    )
                    channels[chat_id] = ChannelSeed(
                        source_name=source.name,
                        raw_id=chat_id,
                        name=new_title,
                        theme_name=new_title,
                    )

    people: dict[str, PersonSeed] = {}
    for recipient_id in sorted(relevant_recipient_ids):
        recipient = recipients.get(recipient_id)
        if recipient is None:
            continue
        raw_id = _recipient_primary_id(recipient, account)
        display_name = _recipient_display_name(recipient, account)
        people[raw_id] = PersonSeed(platform="signal", raw_id=raw_id, display_name=display_name)

    for stable_id in sorted(relevant_stable_ids):
        if stable_id in people:
            continue
        people[stable_id] = PersonSeed(platform="signal", raw_id=stable_id, display_name=stable_id)

    messages: list[MessageSeed] = []
    for record in _jsonl_records(root):
        if "chatItem" not in record:
            continue
        item = record["chatItem"]
        chat_id = str(item.get("chatId") or "")
        if chat_id not in group_chat_ids:
            continue
        author_id = str(item.get("authorId") or "")
        author = recipients.get(author_id)
        raw_id = _recipient_primary_id(author, account) if author else author_id
        person = people.get(raw_id)
        if person is None:
            display_name = _recipient_display_name(author, account) if author else raw_id
            person = PersonSeed(platform="signal", raw_id=raw_id, display_name=display_name)
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
            )
        )

    return SignalExport(
        source=source,
        channels=list(channels.values()),
        messages=messages,
        people=list(people.values()),
        name_changes=name_changes,
    )


def normalize_database(path: Path) -> SignalExport:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        source = _legacy_source(path)

        conversation_rows = con.execute(
            "SELECT id, name, profileName, profileFullName, e164, serviceId, members FROM conversations"
        ).fetchall()
        people_by_key: dict[str, PersonSeed] = {}
        channels: dict[str, ChannelSeed] = {}
        name_changes: list[NameChangeSeed] = []

        for row in conversation_rows:
            channel_name = _conversation_name(row)
            channels[str(row["id"])] = ChannelSeed(
                source_name=source.name,
                raw_id=str(row["id"]),
                name=channel_name,
                theme_name=channel_name,
            )
            for key in ("serviceId", "e164", "id"):
                value = row[key]
                if value:
                    raw_id = str(value)
                    people_by_key.setdefault(
                        raw_id,
                        PersonSeed(platform="signal", raw_id=raw_id, display_name=_display_name(row)),
                    )
            members = row["members"]
            if members:
                try:
                    parsed = json.loads(members)
                except json.JSONDecodeError:
                    parsed = []
                for member in parsed if isinstance(parsed, list) else []:
                    if isinstance(member, dict):
                        for key in ("aci", "e164", "serviceId", "id", "uuid"):
                            value = member.get(key)
                            if value:
                                raw_id = str(value)
                                people_by_key.setdefault(
                                    raw_id,
                                    PersonSeed(platform="signal", raw_id=raw_id, display_name=str(member.get("name") or member.get("profileName") or raw_id)),
                                )
                                break

        event_rows = con.execute(
            "SELECT id, conversationId, timestamp, type, json FROM messages WHERE type IN ('profile-change', 'group-v2-change') ORDER BY timestamp, id"
        ).fetchall()
        for row in event_rows:
            payload = json.loads(row["json"] or "{}")
            ts = to_utc_naive(datetime.fromtimestamp(int(row["timestamp"]) / 1000.0))
            if row["type"] == "profile-change":
                change = payload.get("profileChange") or {}
                if change.get("type") == "name":
                    changed_id = str(payload.get("changedId") or row["conversationId"])
                    new_name = str(change.get("newName") or change.get("oldName") or changed_id)
                    previous_name = change.get("oldName")
                    people_by_key.setdefault(
                        changed_id,
                        PersonSeed(platform="signal", raw_id=changed_id, display_name=new_name),
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
                channel_id = str(row["conversationId"])
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
                                previous_name=str(detail.get("oldTitle")) if detail.get("oldTitle") else None,
                                new_name=new_title,
                                ts=ts,
                                kind="channel-title-change",
                                payload_json=row["json"],
                            )
                        )

        reaction_counts = dict(
            con.execute("SELECT messageId, COUNT(*) FROM reactions GROUP BY messageId").fetchall()
        )
        attachment_counts = dict(
            con.execute("SELECT messageId, COUNT(*) FROM message_attachments GROUP BY messageId").fetchall()
        )
        messages: list[MessageSeed] = []
        rows = con.execute(
            "SELECT id, conversationId, sourceServiceId, body, timestamp, type FROM messages WHERE type IN ('incoming','outgoing') ORDER BY timestamp, id"
        ).fetchall()
        for row in rows:
            conversation_id = str(row["conversationId"])
            channel = channels.get(conversation_id)
            if channel is None:
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
            timestamp = to_utc_naive(datetime.fromtimestamp(int(row["timestamp"]) / 1000.0))
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
                    reaction_count=int(reaction_counts.get(row["id"], 0)),
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


def normalize(path: Path) -> SignalExport:
    if path.is_dir() and (path / "main.jsonl").exists():
        return normalize_backup(path)
    return normalize_database(path)
