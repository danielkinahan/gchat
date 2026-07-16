"""Normalize per-message reaction payloads into queryable event rows."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

import duckdb

from .models import MessageSeed

_SUMMARY_RE = re.compile(r"(\S+)×(\d+)")


def _detail_items(message: MessageSeed) -> list[dict[str, object]]:
    if message.reaction_details_json:
        try:
            parsed = json.loads(message.reaction_details_json)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except (TypeError, ValueError):
            pass
    if message.reaction_summary:
        return [
            {"name": match.group(1), "count": int(match.group(2))}
            for match in _SUMMARY_RE.finditer(message.reaction_summary)
        ]
    return []


def materialize_reaction_facts(
    con: duckdb.DuckDBPyConnection,
    messages: Iterable[MessageSeed],
    person_ids: dict[tuple[str, str], int],
) -> int:
    """Persist one row per reaction and resolve identities where exports allow it."""
    con.execute("DELETE FROM message_reaction_events")
    rows: list[tuple[object, ...]] = []

    for message in messages:
        reaction_index = 0
        represented = 0
        for item in _detail_items(message):
            emoji = str(item.get("name") or "").strip() or "unknown"
            try:
                count = max(int(item.get("count") or 0), 0)
            except (TypeError, ValueError):
                count = 0
            count = min(count, max(message.reaction_count - represented, 0))
            emoji_id = str(item.get("emoji_id") or "").strip() or None
            image_url = str(item.get("image_url") or "").strip() or None
            code = str(item.get("code") or "").strip() or None
            is_animated = bool(item.get("is_animated"))
            reactors = item.get("reactors")
            reactor_items = reactors if isinstance(reactors, list) else []
            identified = 0
            for reactor in reactor_items[:count]:
                if not isinstance(reactor, dict):
                    continue
                platform = str(reactor.get("platform") or message.person.platform)
                raw_id = str(reactor.get("raw_id") or "").strip()
                display_name = str(reactor.get("display_name") or "").strip()
                if not raw_id:
                    continue
                rows.append(
                    (
                        message.id,
                        reaction_index,
                        emoji,
                        platform,
                        raw_id,
                        display_name or None,
                        person_ids.get((platform, raw_id)),
                        emoji_id,
                        image_url,
                        code,
                        is_animated,
                    )
                )
                reaction_index += 1
                identified += 1
            for _ in range(max(count - identified, 0)):
                rows.append(
                    (
                        message.id,
                        reaction_index,
                        emoji,
                        None,
                        None,
                        None,
                        None,
                        emoji_id,
                        image_url,
                        code,
                        is_animated,
                    )
                )
                reaction_index += 1
            represented += count

        for _ in range(max(message.reaction_count - represented, 0)):
            rows.append(
                (
                    message.id,
                    reaction_index,
                    "unknown",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    False,
                )
            )
            reaction_index += 1

    if rows:
        con.executemany(
            "INSERT INTO message_reaction_events "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    con.execute("DROP INDEX IF EXISTS message_reaction_events_person_idx")
    con.execute("DROP INDEX IF EXISTS message_reaction_events_emoji_idx")
    con.execute(
        "CREATE INDEX message_reaction_events_person_idx "
        "ON message_reaction_events (reactor_person_id, message_id)"
    )
    con.execute(
        "CREATE INDEX message_reaction_events_emoji_idx "
        "ON message_reaction_events (emoji, message_id)"
    )
    con.execute(
        """
        INSERT OR REPLACE INTO build_metadata VALUES (
            'reaction_identity_version',
            'reaction-events-v2'
        )
        """
    )
    return len(rows)
