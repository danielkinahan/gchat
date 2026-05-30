from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .discord import normalize_export as normalize_discord
from .discovery import discover_dataset
from .facebook import normalize_chat as normalize_facebook
from .models import ChannelSeed, MessageSeed, NameChangeSeed, PersonSeed, SourceSeed
from .reconciliation import load_reconciliation
from .schema import SCHEMA_SQL
from .signal import normalize as normalize_signal
from .util import message_counts, stable_color


@dataclass(frozen=True)
class NormalizedDataset:
    sources: list[SourceSeed]
    channels: list[ChannelSeed]
    people: list[PersonSeed]
    name_changes: list[NameChangeSeed]
    messages: list[MessageSeed]


def _collect(data_dir: Path) -> NormalizedDataset:
    paths = discover_dataset(data_dir)
    sources: "OrderedDict[str, SourceSeed]" = OrderedDict()
    channels: "OrderedDict[tuple[str, str], ChannelSeed]" = OrderedDict()
    people: "OrderedDict[tuple[str, str], PersonSeed]" = OrderedDict()
    name_changes: list[NameChangeSeed] = []
    messages: list[MessageSeed] = []

    for path in paths.discord_files:
        export = normalize_discord(path)
        sources.setdefault(export.source.name, export.source)
        channels.setdefault((export.channel.source_name, export.channel.raw_id), export.channel)
        for person in export.people:
            people.setdefault((person.platform, person.raw_id), person)
        name_changes.extend(export.name_changes)
        messages.extend(export.messages)

    for chat_dir in paths.facebook_chats:
        export = normalize_facebook(chat_dir)
        sources.setdefault(export.source.name, export.source)
        channels.setdefault((export.channel.source_name, export.channel.raw_id), export.channel)
        for person in export.people:
            people.setdefault((person.platform, person.raw_id), person)
        name_changes.extend(export.name_changes)
        messages.extend(export.messages)

    for path in paths.signal_dbs + paths.signal_exports:
        export = normalize_signal(path)
        sources.setdefault(export.source.name, export.source)
        for channel in export.channels:
            channels.setdefault((channel.source_name, channel.raw_id), channel)
        for person in export.people:
            people.setdefault((person.platform, person.raw_id), person)
        name_changes.extend(export.name_changes)
        messages.extend(export.messages)

    return NormalizedDataset(
        sources=list(sources.values()),
        channels=list(channels.values()),
        people=list(people.values()),
        name_changes=name_changes,
        messages=messages,
    )


def build_database(data_dir: Path, output_path: Path, overwrite: bool = True) -> None:
    if overwrite and output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dataset = _collect(data_dir)
    reconciliation = load_reconciliation(Path.cwd())
    con = duckdb.connect(str(output_path))
    con.execute(SCHEMA_SQL)

    people_rows = []
    identity_rows = []
    canonical_people: dict[tuple[str, str], int] = {}
    person_ids: dict[tuple[str, str], int] = {}
    for person in sorted(dataset.people, key=lambda p: (p.platform, p.raw_id)):
        canonical_name, color_hint = reconciliation.people.resolve(person.platform, person.raw_id, person.display_name)
        canonical_key = (canonical_name, color_hint)
        person_id = canonical_people.get(canonical_key)
        if person_id is None:
            person_id = len(canonical_people) + 1
            canonical_people[canonical_key] = person_id
            people_rows.append((person_id, canonical_name, color_hint or stable_color(canonical_name)))
        person_ids[(person.platform, person.raw_id)] = person_id
        identity_rows.append((person.platform, person.raw_id, person.display_name, person_id))

    source_ids: dict[str, int] = {}
    source_rows = []
    for idx, source in enumerate(sorted(dataset.sources, key=lambda s: s.name), start=1):
        source_ids[source.name] = idx
        source_rows.append((idx, source.platform, source.name))

    theme_ids: dict[str, int] = {}
    channel_ids: dict[tuple[str, str], int] = {}
    channel_rows = []
    theme_rows = []
    for idx, channel in enumerate(sorted(dataset.channels, key=lambda c: (c.source_name, c.raw_id)), start=1):
        channel_ids[(channel.source_name, channel.raw_id)] = idx
        theme_name = reconciliation.themes.resolve(channel.source_name, channel.name)
        if theme_name not in theme_ids:
            theme_ids[theme_name] = len(theme_ids) + 1
            theme_rows.append((theme_ids[theme_name], theme_name))
        theme_id = theme_ids[theme_name]
        channel_rows.append((idx, source_ids[channel.source_name], channel.raw_id, channel.name, theme_id))

    message_rows = []
    seen_message_ids: set[str] = set()
    for message in sorted(dataset.messages, key=lambda m: (m.ts, m.id)):
        if message.id in seen_message_ids:
            continue
        seen_message_ids.add(message.id)
        person_id = person_ids[(message.person.platform, message.person.raw_id)]
        channel_id = channel_ids[(message.source_name, message.channel_raw_id)]
        word_count, char_count = message_counts(message.content)
        message_rows.append(
            (
                message.id,
                channel_id,
                person_id,
                message.ts,
                message.content,
                message.reply_to_id,
                word_count,
                char_count,
                message.attachment_count,
                message.reaction_count,
                None,
                None,
            )
        )

    person_name_change_rows = []
    channel_name_change_rows = []
    for idx, change in enumerate(sorted(dataset.name_changes, key=lambda c: (c.ts, c.platform, c.entity_kind, c.entity_raw_id, c.kind)), start=1):
        source_id = source_ids[change.source_name]
        if change.entity_kind == "person":
            person_id = person_ids[(change.platform, change.entity_raw_id)]
            person_name_change_rows.append(
                (idx, person_id, source_id, change.kind, change.previous_name, change.new_name, change.ts, change.payload_json)
            )
        elif change.entity_kind == "channel":
            channel_id = channel_ids[(change.source_name, change.entity_raw_id)]
            channel_name_change_rows.append(
                (idx, channel_id, source_id, change.kind, change.previous_name, change.new_name, change.ts, change.payload_json)
            )

    if people_rows:
        con.executemany("INSERT INTO people VALUES (?, ?, ?)", people_rows)
    if identity_rows:
        con.executemany("INSERT INTO platform_identities VALUES (?, ?, ?, ?)", identity_rows)
    if source_rows:
        con.executemany("INSERT INTO sources VALUES (?, ?, ?)", source_rows)
    if theme_rows:
        con.executemany("INSERT INTO themes VALUES (?, ?)", theme_rows)
    if channel_rows:
        con.executemany("INSERT INTO channels VALUES (?, ?, ?, ?, ?)", channel_rows)
    if person_name_change_rows:
        con.executemany("INSERT INTO person_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", person_name_change_rows)
    if channel_name_change_rows:
        con.executemany("INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", channel_name_change_rows)
    if message_rows:
        con.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", message_rows)
    con.close()
