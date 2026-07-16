from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb

from .discord import normalize_export as normalize_discord
from .discovery import discover_dataset
from .facebook import normalize_chat as normalize_facebook
from .models import (
    ChannelSeed,
    MemberEventSeed,
    MessageSeed,
    NameChangeSeed,
    PersonSeed,
    SourceSeed,
)
from .person_stats import refresh_person_stats
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
    member_events: list[MemberEventSeed]


def _notify(status: Callable[[str], None] | None, message: str) -> None:
    if status is not None:
        status(message)


def _collect(
    data_dir: Path,
    reconciliation,
    filter_signal_to_configured_people: bool,
    status: Callable[[str], None] | None = None,
) -> NormalizedDataset:
    paths = discover_dataset(data_dir)
    sources: "OrderedDict[str, SourceSeed]" = OrderedDict()
    channels: "OrderedDict[tuple[str, str], ChannelSeed]" = OrderedDict()
    people: "OrderedDict[tuple[str, str], PersonSeed]" = OrderedDict()
    name_changes: list[NameChangeSeed] = []
    messages: list[MessageSeed] = []
    member_events: list[MemberEventSeed] = []

    _notify(status, f"Scanning {data_dir}")
    for path in paths.discord_files:
        _notify(status, f"Normalizing Discord export {path.name}")
        export = normalize_discord(path)
        sources.setdefault(export.source.name, export.source)
        channels.setdefault(
            (export.channel.source_name, export.channel.raw_id), export.channel
        )
        for person in export.people:
            people.setdefault((person.platform, person.raw_id), person)
        name_changes.extend(export.name_changes)
        messages.extend(export.messages)

    for chat_dir in paths.facebook_chats:
        _notify(status, f"Normalizing Facebook archive {chat_dir.name}")
        export = normalize_facebook(chat_dir)
        sources.setdefault(export.source.name, export.source)
        channels.setdefault(
            (export.channel.source_name, export.channel.raw_id), export.channel
        )
        for person in export.people:
            people.setdefault((person.platform, person.raw_id), person)
        name_changes.extend(export.name_changes)
        messages.extend(export.messages)
        member_events.extend(export.member_events)

    for path in paths.signal_exports:
        _notify(status, f"Normalizing Signal HTML export {path.name}")
        export = normalize_signal(
            path,
            status=status,
            reconciliation=reconciliation,
            filter_to_configured_people=filter_signal_to_configured_people,
        )
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
        member_events=member_events,
    )


def build_database(
    data_dir: Path,
    output_path: Path,
    overwrite: bool = True,
    status: Callable[[str], None] | None = None,
    config_dir: Path | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Database already exists: {output_path}")

    # Build to a temporary file so the API can continue serving the old DB
    temp_output = output_path.with_suffix(output_path.suffix + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    _notify(status, "Loading reconciliation")
    reconciliation = load_reconciliation(config_dir=config_dir)
    _notify(status, "Collecting exports")
    dataset = _collect(
        data_dir,
        reconciliation=reconciliation,
        filter_signal_to_configured_people=(config_dir / "people.yaml").exists()
        if config_dir is not None
        else False,
        status=status,
    )
    _notify(
        status,
        f"Collected {len(dataset.messages)} messages from {len(dataset.sources)} sources",
    )
    con = duckdb.connect(str(temp_output))
    con.execute(SCHEMA_SQL)

    # Optimize for bulk loading
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA memory_limit='4GB'")

    _notify(status, "Writing DuckDB tables")

    people_rows = []
    identity_rows = []
    canonical_people: dict[tuple[str, str], int] = {}
    person_ids: dict[tuple[str, str], int] = {}
    for person in sorted(dataset.people, key=lambda p: (p.platform, p.raw_id)):
        canonical_name, color_hint = reconciliation.people.resolve(
            person.platform, person.raw_id, person.display_name
        )
        canonical_key = (canonical_name, color_hint)
        person_id = canonical_people.get(canonical_key)
        if person_id is None:
            person_id = len(canonical_people) + 1
            canonical_people[canonical_key] = person_id
            people_rows.append(
                (person_id, canonical_name, color_hint or stable_color(canonical_name))
            )
        person_ids[(person.platform, person.raw_id)] = person_id
        identity_rows.append(
            (person.platform, person.raw_id, person.display_name, person_id)
        )

    source_ids: dict[str, int] = {}
    source_rows = []
    for idx, source in enumerate(
        sorted(dataset.sources, key=lambda s: s.name), start=1
    ):
        source_ids[source.name] = idx
        source_rows.append((idx, source.platform, source.name))

    theme_ids: dict[str, int] = {}
    channel_ids: dict[tuple[str, str], int] = {}
    channel_rows = []
    theme_rows = []
    for idx, channel in enumerate(
        sorted(dataset.channels, key=lambda c: (c.source_name, c.raw_id)), start=1
    ):
        channel_ids[(channel.source_name, channel.raw_id)] = idx
        theme_name = reconciliation.themes.resolve(channel.source_name, channel.name)
        if theme_name not in theme_ids:
            theme_ids[theme_name] = len(theme_ids) + 1
            theme_rows.append((theme_ids[theme_name], theme_name))
        theme_id = theme_ids[theme_name]
        channel_rows.append(
            (
                idx,
                source_ids[channel.source_name],
                channel.raw_id,
                channel.name,
                theme_id,
            )
        )

    # Group messages by channel/time first so we can assign conversation ids.
    # A conversation is a run of messages within the same channel where no two
    # consecutive messages are more than 30 minutes apart.
    CONVERSATION_GAP_SECONDS = 30 * 60

    deduped_messages = []
    seen_message_ids: set[str] = set()
    for message in sorted(dataset.messages, key=lambda m: (m.ts, m.id)):
        if message.id in seen_message_ids:
            continue
        seen_message_ids.add(message.id)
        deduped_messages.append(message)

    # Bucket by channel id so we can walk each channel in chronological order.
    per_channel: dict[int, list] = {}
    for message in deduped_messages:
        channel_id = channel_ids[(message.source_name, message.channel_raw_id)]
        per_channel.setdefault(channel_id, []).append(message)

    conversation_id_for_message: dict[str, int] = {}
    conversation_rows: list[tuple[int, int, object, object, int, int]] = []
    next_conversation_id = 1
    for channel_id, channel_messages in per_channel.items():
        channel_messages.sort(key=lambda m: (m.ts, m.id))
        current_conversation_id: int | None = None
        current_start = None
        current_end = None
        current_count = 0
        current_people: set[tuple[str, str]] = set()
        last_ts = None
        for message in channel_messages:
            gap = (
                (message.ts - last_ts).total_seconds() if last_ts is not None else None
            )
            if current_conversation_id is None or (
                gap is not None and gap > CONVERSATION_GAP_SECONDS
            ):
                if current_conversation_id is not None:
                    conversation_rows.append(
                        (
                            current_conversation_id,
                            channel_id,
                            current_start,
                            current_end,
                            current_count,
                            len(current_people),
                        )
                    )
                current_conversation_id = next_conversation_id
                next_conversation_id += 1
                current_start = message.ts
                current_count = 0
                current_people = set()
            conversation_id_for_message[message.id] = current_conversation_id
            current_end = message.ts
            current_count += 1
            current_people.add((message.person.platform, message.person.raw_id))
            last_ts = message.ts
        if current_conversation_id is not None:
            conversation_rows.append(
                (
                    current_conversation_id,
                    channel_id,
                    current_start,
                    current_end,
                    current_count,
                    len(current_people),
                )
            )

    message_rows = []
    for message in deduped_messages:
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
                message.attachment_preview,
                message.reaction_count,
                message.reaction_summary,
                message.reaction_details_json,
                message.is_edited,
                message.is_system,
                None,
                None,
                conversation_id_for_message.get(message.id),
            )
        )

    person_name_change_rows = []
    channel_name_change_rows = []
    for idx, change in enumerate(
        sorted(
            dataset.name_changes,
            key=lambda c: (c.ts, c.platform, c.entity_kind, c.entity_raw_id, c.kind),
        ),
        start=1,
    ):
        source_id = source_ids[change.source_name]
        if change.entity_kind == "person":
            person_id = person_ids[(change.platform, change.entity_raw_id)]
            person_name_change_rows.append(
                (
                    idx,
                    person_id,
                    source_id,
                    change.kind,
                    change.previous_name,
                    change.new_name,
                    change.ts,
                    change.payload_json,
                )
            )
        elif change.entity_kind == "channel":
            channel_id = channel_ids[(change.source_name, change.entity_raw_id)]
            channel_name_change_rows.append(
                (
                    idx,
                    channel_id,
                    source_id,
                    change.kind,
                    change.previous_name,
                    change.new_name,
                    change.ts,
                    change.payload_json,
                )
            )

    con.execute("BEGIN TRANSACTION")

    if people_rows:
        _notify(status, f"  Writing people ({len(people_rows)} rows)")
        con.executemany("INSERT INTO people VALUES (?, ?, ?)", people_rows)
    if identity_rows:
        _notify(status, f"  Writing platform identities ({len(identity_rows)} rows)")
        con.executemany(
            "INSERT INTO platform_identities VALUES (?, ?, ?, ?)", identity_rows
        )
    if source_rows:
        _notify(status, f"  Writing sources ({len(source_rows)} rows)")
        con.executemany("INSERT INTO sources VALUES (?, ?, ?)", source_rows)
    if theme_rows:
        _notify(status, f"  Writing themes ({len(theme_rows)} rows)")
        con.executemany("INSERT INTO themes VALUES (?, ?)", theme_rows)
    if channel_rows:
        _notify(status, f"  Writing channels ({len(channel_rows)} rows)")
        con.executemany("INSERT INTO channels VALUES (?, ?, ?, ?, ?)", channel_rows)
    if person_name_change_rows:
        _notify(
            status,
            f"  Writing person name changes ({len(person_name_change_rows)} rows)",
        )
        con.executemany(
            "INSERT INTO person_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            person_name_change_rows,
        )
    if channel_name_change_rows:
        _notify(
            status,
            f"  Writing channel name changes ({len(channel_name_change_rows)} rows)",
        )
        con.executemany(
            "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            channel_name_change_rows,
        )
    if conversation_rows:
        _notify(status, f"  Writing conversations ({len(conversation_rows)} rows)")
        con.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
            conversation_rows,
        )
    member_event_rows = []
    for idx, event in enumerate(
        sorted(
            dataset.member_events,
            key=lambda e: (e.ts, e.kind, e.target_raw_id),
        ),
        start=1,
    ):
        channel_key = (event.source_name, event.channel_raw_id)
        channel_id = channel_ids.get(channel_key)
        target_id = person_ids.get((event.platform, event.target_raw_id))
        if channel_id is None or target_id is None:
            continue
        actor_id = (
            person_ids.get((event.platform, event.actor_raw_id))
            if event.actor_raw_id
            else None
        )
        source_id = source_ids[event.source_name]
        member_event_rows.append(
            (
                idx,
                channel_id,
                source_id,
                event.kind,
                actor_id,
                target_id,
                event.ts,
                event.payload_json,
            )
        )
    if member_event_rows:
        _notify(status, f"  Writing member events ({len(member_event_rows)} rows)")
        con.executemany(
            "INSERT INTO member_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            member_event_rows,
        )
    if message_rows:
        _notify(status, f"  Writing messages ({len(message_rows)} rows)")
        batch_size = 50000
        for i in range(0, len(message_rows), batch_size):
            batch = message_rows[i : i + batch_size]
            con.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            _notify(
                status,
                f"    Inserted {min(i + batch_size, len(message_rows))}/{len(message_rows)} messages",
            )

    _notify(status, "Computing person diversity stats")
    stats_count = refresh_person_stats(con, has_is_system=True)
    _notify(status, f"  Wrote person stats ({stats_count} rows)")

    con.execute("COMMIT")
    con.close()

    # Atomic on a single filesystem, so readers never observe a missing DB path.
    temp_output.replace(output_path)

    _notify(status, f"Build complete: {output_path}")
