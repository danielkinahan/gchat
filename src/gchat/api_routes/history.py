"""Name history and member event routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import duckdb
from fastapi import FastAPI, Query

from ..api_filters import QueryFilters, csv_ints, csv_strings


def _connect(app: FastAPI) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(app.state.db_path), read_only=True)


def _get_display_name(
    channel_name: str, source_name: str, fb_chat_names: dict[str, str]
) -> str:
    if source_name.startswith("Facebook: "):
        display_name = fb_chat_names.get(channel_name)
        if display_name:
            return display_name
    return channel_name


def _normalized_history_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).casefold()


def _format_history_actor_name(
    actor_name: str | None,
    actor_raw_id: str | None,
    platform: str,
    identity_to_display_name: dict[tuple[str, str], str],
    actor_nickname: str | None = None,
    you_fallback_name: str | None = None,
) -> str | None:
    def _replace_you_placeholder(
        value: str, canonical_name: str | None, fallback_name: str | None
    ) -> str:
        replacement_name = canonical_name
        if _normalized_history_name(replacement_name) == "you":
            replacement_name = None
        replacement_name = replacement_name or fallback_name
        normalized = _normalized_history_name(value)
        if normalized == "you" and replacement_name:
            return replacement_name
        if "(you)" in value.casefold() and replacement_name:
            replaced = value.replace("(You)", f"({replacement_name})")
            replaced = replaced.replace("(you)", f"({replacement_name})")
            return " ".join(replaced.split())
        return value

    resolved = (
        identity_to_display_name.get((platform, actor_raw_id)) if actor_raw_id else None
    )
    if _normalized_history_name(resolved) == "you":
        resolved = you_fallback_name or None
    display_actor = actor_nickname or resolved or actor_name
    if display_actor:
        display_actor = _replace_you_placeholder(
            display_actor, resolved, you_fallback_name
        )
    if display_actor and resolved:
        if f"({resolved})".casefold() in display_actor.casefold():
            return display_actor
        if _normalized_history_name(display_actor) == _normalized_history_name(
            resolved
        ):
            return display_actor
        return f"{display_actor} ({resolved})"
    if display_actor:
        return display_actor
    return resolved


def register_history_routes(app: FastAPI) -> None:
    @app.get("/api/name-history")
    def name_history(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Channel rename history and person nickname history."""
        people_filter = csv_ints(people, "people")
        platforms_filter = csv_strings(platforms)

        with _connect(app) as con:
            channel_params: list[Any] = []
            channel_where = ["1 = 1"]
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                channel_where.append(f"s.platform IN ({placeholders})")
                channel_params.extend(platforms_filter)

            channel_rows = con.execute(
                f"""
                SELECT
                    c.id,
                    c.source_id,
                    s.platform,
                    s.name AS source_name,
                    c.name AS current_name,
                    c.platform_channel_id
                FROM channels c
                JOIN sources s ON s.id = c.source_id
                WHERE {" AND ".join(channel_where)}
                ORDER BY s.platform, s.name, c.name
                """,
                channel_params,
            ).fetchall()

            channel_change_params: list[Any] = []
            channel_change_where = ["trim(d.new_name) <> ''"]
            if start is not None:
                channel_change_where.append("d.ts >= ?")
                channel_change_params.append(datetime.combine(start, time.min))
            if end is not None:
                channel_change_where.append("d.ts < ?")
                channel_change_params.append(
                    datetime.combine(end + timedelta(days=1), time.min)
                )
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                channel_change_where.append(f"s.platform IN ({placeholders})")
                channel_change_params.extend(platforms_filter)

            channel_change_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT
                        channel_id,
                        source_id,
                        previous_name,
                        new_name,
                        ts,
                        json_extract_string(payload_json, '$.actor_name') AS actor_name,
                        coalesce(
                            json_extract_string(payload_json, '$.actor_raw_id'),
                            json_extract_string(payload_json, '$.updateMessage.groupChange.updates[0].groupNameUpdate.updaterAci')
                        ) AS actor_raw_id
                    FROM channel_name_changes
                )
                SELECT d.channel_id, d.source_id, s.platform, c.platform_channel_id, d.previous_name, d.new_name, d.ts, d.actor_name, d.actor_raw_id
                FROM deduped d
                JOIN channels c ON c.id = d.channel_id
                JOIN sources s ON s.id = d.source_id
                WHERE {" AND ".join(channel_change_where)}
                ORDER BY d.channel_id, d.ts, d.previous_name, d.new_name
                """,
                channel_change_params,
            ).fetchall()

            person_change_params: list[Any] = []
            person_change_where = ["1 = 1"]
            if start is not None:
                person_change_where.append("d.ts >= ?")
                person_change_params.append(datetime.combine(start, time.min))
            if end is not None:
                person_change_where.append("d.ts < ?")
                person_change_params.append(
                    datetime.combine(end + timedelta(days=1), time.min)
                )
            if people_filter:
                placeholders = ", ".join("?" for _ in people_filter)
                person_change_where.append(f"d.person_id IN ({placeholders})")
                person_change_params.extend(people_filter)
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                person_change_where.append(f"s.platform IN ({placeholders})")
                person_change_params.extend(platforms_filter)

            person_change_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT
                        person_id,
                        source_id,
                        json_extract_string(payload_json, '$.chatId') AS chat_id,
                        json_extract_string(payload_json, '$.actor_name') AS actor_name,
                        json_extract_string(payload_json, '$.actor_raw_id') AS actor_raw_id,
                        previous_name,
                        new_name,
                        ts
                    FROM person_name_changes
                )
                SELECT d.person_id, d.source_id, s.platform, d.chat_id, p.display_name, d.actor_name, d.actor_raw_id, d.previous_name, d.new_name, d.ts
                FROM deduped d
                JOIN people p ON p.id = d.person_id
                JOIN sources s ON s.id = d.source_id
                WHERE {" AND ".join(person_change_where)}
                ORDER BY d.source_id, d.chat_id, p.display_name, d.ts, d.previous_name, d.new_name
                """,
                person_change_params,
            ).fetchall()

            nickname_timeline_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT
                        person_id,
                        source_id,
                        json_extract_string(payload_json, '$.chatId') AS chat_id,
                        new_name,
                        ts
                    FROM person_name_changes
                )
                SELECT d.person_id, d.source_id, s.platform, d.chat_id, d.new_name, d.ts
                FROM deduped d
                JOIN sources s ON s.id = d.source_id
                WHERE {" AND ".join(["1 = 1"] + ([f"s.platform IN ({', '.join('?' for _ in platforms_filter)})"] if platforms_filter else []))}
                ORDER BY d.source_id, d.chat_id, d.person_id, d.ts
                """,
                platforms_filter if platforms_filter else [],
            ).fetchall()

            identity_rows = con.execute(
                """
                SELECT pi.platform, pi.platform_user_id, p.id, p.display_name
                FROM platform_identities pi
                JOIN people p ON p.id = pi.person_id
                """
            ).fetchall()
            preferred_name_by_person_id: dict[int, str] = {}
            for platform, platform_user_id, person_id, display_name in identity_rows:
                key = (str(platform), str(platform_user_id))
                configured = app.state.reconciliation.people.identity_to_person.get(key)
                if configured:
                    preferred_name_by_person_id[int(person_id)] = configured[0]
                    continue
                candidate_name = str(display_name)
                if _normalized_history_name(candidate_name) not in {"", "you"}:
                    preferred_name_by_person_id.setdefault(
                        int(person_id), candidate_name
                    )
                    continue
                candidate_id = str(platform_user_id)
                if _normalized_history_name(candidate_id) not in {"", "you"}:
                    preferred_name_by_person_id.setdefault(int(person_id), candidate_id)
            identity_to_display_name = {
                (str(platform), str(platform_user_id)): str(display_name)
                for platform, platform_user_id, _, display_name in identity_rows
            }
            for (platform, raw_id), (
                configured_name,
                _color,
            ) in app.state.reconciliation.people.identity_to_person.items():
                key = (str(platform), str(raw_id))
                existing = identity_to_display_name.get(key)
                if existing is None or _normalized_history_name(existing) == "you":
                    identity_to_display_name[key] = configured_name
            for platform, platform_user_id, person_id, _display_name in identity_rows:
                key = (str(platform), str(platform_user_id))
                existing = identity_to_display_name.get(key)
                if existing and _normalized_history_name(existing) == "you":
                    preferred_name = preferred_name_by_person_id.get(int(person_id))
                    if preferred_name:
                        identity_to_display_name[key] = preferred_name
            identity_to_person_id = {
                (str(platform), str(platform_user_id)): int(person_id)
                for platform, platform_user_id, person_id, _ in identity_rows
            }

            nickname_timeline: dict[
                tuple[str, int, str, int], list[tuple[datetime | None, str]]
            ] = {}
            for (
                person_id,
                source_id,
                platform,
                chat_id,
                new_name,
                ts,
            ) in nickname_timeline_rows:
                if not chat_id:
                    continue
                key = (str(platform), int(source_id), str(chat_id), int(person_id))
                nickname_timeline.setdefault(key, []).append((ts, str(new_name or "")))
            for key in nickname_timeline:
                nickname_timeline[key].sort(key=lambda item: item[0] or datetime.min)

            def person_display_name(person_id: int, fallback_display_name: str) -> str:
                preferred = preferred_name_by_person_id.get(person_id)
                if preferred:
                    return preferred
                if (
                    _normalized_history_name(fallback_display_name) == "you"
                    and app.state.primary_person_name
                ):
                    return app.state.primary_person_name
                return fallback_display_name

            def actor_nickname_at(
                platform: str,
                source_id: int,
                chat_id: str | None,
                actor_raw_id: str | None,
                ts: datetime | None,
            ) -> str | None:
                if not chat_id or not actor_raw_id or ts is None:
                    return None
                person_id = identity_to_person_id.get((platform, actor_raw_id))
                if person_id is None:
                    return None
                events = nickname_timeline.get(
                    (platform, source_id, str(chat_id), person_id), []
                )
                nickname: str | None = None
                for event_ts, event_new_name in events:
                    if event_ts is None or event_ts > ts:
                        break
                    normalized = _normalized_history_name(event_new_name)
                    nickname = (
                        None if normalized in {"", "(cleared)"} else event_new_name
                    )
                return nickname

            channel_history_by_id: dict[int, list[dict[str, Any]]] = {}
            for (
                channel_id,
                source_id,
                platform,
                platform_chat_id,
                previous_name,
                new_name,
                ts,
                actor_name,
                actor_raw_id,
            ) in channel_change_rows:
                historical_actor_nickname = actor_nickname_at(
                    str(platform),
                    int(source_id),
                    str(platform_chat_id) if platform_chat_id is not None else None,
                    actor_raw_id,
                    ts,
                )
                channel_history_by_id.setdefault(int(channel_id), []).append(
                    {
                        "previous_name": previous_name,
                        "new_name": new_name,
                        "author_name": _format_history_actor_name(
                            actor_name,
                            actor_raw_id,
                            str(platform),
                            identity_to_display_name,
                            actor_nickname=historical_actor_nickname,
                            you_fallback_name=app.state.primary_person_name,
                        ),
                        "ts": ts.isoformat() if ts else None,
                    }
                )

            participants_by_chat: dict[
                tuple[int, str], dict[int, dict[str, Any]]
            ] = {}
            for (
                person_id,
                source_id,
                platform,
                chat_id,
                display_name,
                actor_name,
                actor_raw_id,
                previous_name,
                new_name,
                ts,
            ) in person_change_rows:
                if not chat_id:
                    continue
                chat_key = (int(source_id), str(chat_id))
                person_entry = participants_by_chat.setdefault(chat_key, {}).setdefault(
                    int(person_id),
                    {
                        "id": int(person_id),
                        "display_name": person_display_name(
                            int(person_id), str(display_name)
                        ),
                        "history": [],
                    },
                )
                person_entry["history"].append(
                    {
                        "previous_name": previous_name,
                        "new_name": new_name,
                        "author_name": _format_history_actor_name(
                            actor_name,
                            actor_raw_id,
                            str(platform),
                            identity_to_display_name,
                            actor_nickname=actor_nickname_at(
                                str(platform),
                                int(source_id),
                                str(chat_id),
                                actor_raw_id,
                                ts,
                            ),
                            you_fallback_name=app.state.primary_person_name,
                        )
                        or display_name,
                        "ts": ts.isoformat() if ts else None,
                    }
                )

            chats: list[dict[str, Any]] = []
            signal_chat_ids: set[int] = set()
            if app.state.configured_people_names:
                signal_chat_rows = con.execute(
                    """
                    SELECT c.id, COUNT(DISTINCT p.id) AS configured_people_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON s.id = c.source_id
                    JOIN people p ON p.id = m.person_id
                    WHERE s.platform = 'signal'
                      AND p.display_name IN ({})
                    GROUP BY c.id
                    """.format(
                        ", ".join("?" for _ in app.state.configured_people_names)
                    ),
                    sorted(app.state.configured_people_names),
                ).fetchall()
                signal_chat_ids = {
                    int(row[0]) for row in signal_chat_rows if int(row[1]) >= 2
                }

            for (
                channel_id,
                source_id,
                platform,
                source_name,
                current_name,
                platform_channel_id,
            ) in channel_rows:
                chat_key = (int(source_id), str(platform_channel_id))
                raw_previous_names = channel_history_by_id.get(int(channel_id), [])
                if not raw_previous_names:
                    continue
                previous_names: list[dict[str, Any]] = []
                has_real_rename = False
                for change in raw_previous_names:
                    previous_norm = _normalized_history_name(change["previous_name"])
                    new_norm = _normalized_history_name(change["new_name"])
                    if not new_norm:
                        continue
                    if previous_norm and previous_norm != new_norm:
                        has_real_rename = True
                    if not previous_norm or previous_norm != new_norm:
                        previous_names.append(change)
                if not has_real_rename or not previous_names:
                    continue
                if platform == "signal" and int(channel_id) not in signal_chat_ids:
                    continue
                participants = [
                    participant
                    for participant in sorted(
                        participants_by_chat.get(chat_key, {}).values(),
                        key=lambda item: item["display_name"].casefold(),
                    )
                    if participant["history"]
                ]
                chats.append(
                    {
                        "id": int(channel_id),
                        "platform": platform,
                        "source_name": source_name,
                        "current_name": _get_display_name(
                            current_name, source_name, app.state.fb_chat_names
                        ),
                        "platform_channel_id": platform_channel_id,
                        "previous_names": previous_names,
                        "participants": participants,
                    }
                )

            return {"chats": chats}

    @app.get("/api/member-events")
    def member_events(
        kind: str | None = Query(default=None, pattern="^(added|removed|left)$"),
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate kicks/leaves/adds by actor (kicker), target (kickee), and chat.

        Returns three rankings filtered by the same query params as the rest of
        the API. The `people` filter applies to *both* actor and target so a
        person-scoped view includes events they were involved in either way.
        """
        with _connect(app) as con:
            has_table = con.execute(
                """
                SELECT 1 FROM information_schema.tables WHERE table_name = 'member_events'
                """
            ).fetchone()
        if not has_table:
            return {"kind": kind, "by_actor": [], "by_target": [], "by_chat": []}

        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
        )

        clauses: list[str] = ["1 = 1"]
        params: list[Any] = []
        if start is not None:
            clauses.append("e.ts >= ?")
            params.append(datetime.combine(start, time.min))
        if end is not None:
            clauses.append("e.ts < ?")
            params.append(datetime.combine(end + timedelta(days=1), time.min))
        if kind:
            clauses.append("e.kind = ?")
            params.append(kind)
        if filters.platforms:
            placeholders = ", ".join("?" for _ in filters.platforms)
            clauses.append(f"s.platform IN ({placeholders})")
            params.extend(filters.platforms)
        if filters.themes:
            theme_names = {
                app.state.theme_id_to_name.get(theme_id) for theme_id in filters.themes
            }
            theme_names.discard(None)
            channel_ids = sorted(
                {
                    channel_id
                    for theme_name in theme_names
                    for channel_id in app.state.theme_to_channel_ids.get(theme_name, [])
                }
            )
            if not channel_ids:
                return {"kind": kind, "by_actor": [], "by_target": [], "by_chat": []}
            placeholders = ", ".join("?" for _ in channel_ids)
            clauses.append(f"e.channel_id IN ({placeholders})")
            params.extend(channel_ids)
        if filters.people:
            placeholders = ", ".join("?" for _ in filters.people)
            clauses.append(
                f"(e.actor_person_id IN ({placeholders}) OR e.target_person_id IN ({placeholders}))"
            )
            params.extend(filters.people)
            params.extend(filters.people)
        where = " AND ".join(clauses)

        with _connect(app) as con:
            by_actor_rows = con.execute(
                f"""
                SELECT
                    MIN(p.id) AS id,
                    ANY_VALUE(p.display_name) AS display_name,
                    ANY_VALUE(p.color) AS color,
                    COUNT(*) AS count
                FROM member_events e
                JOIN channels c ON c.id = e.channel_id
                JOIN sources s ON s.id = c.source_id
                JOIN people p ON p.id = e.actor_person_id
                WHERE {where} AND e.actor_person_id IS NOT NULL
                GROUP BY LOWER(TRIM(p.display_name))
                ORDER BY count DESC, MIN(p.display_name)
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            by_target_rows = con.execute(
                f"""
                SELECT
                    MIN(p.id) AS id,
                    ANY_VALUE(p.display_name) AS display_name,
                    ANY_VALUE(p.color) AS color,
                    COUNT(*) AS count
                FROM member_events e
                JOIN channels c ON c.id = e.channel_id
                JOIN sources s ON s.id = c.source_id
                JOIN people p ON p.id = e.target_person_id
                WHERE {where}
                GROUP BY LOWER(TRIM(p.display_name))
                ORDER BY count DESC, MIN(p.display_name)
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            by_chat_rows = con.execute(
                f"""
                SELECT
                    c.id, c.name, s.name AS source_name, s.platform, COUNT(*) AS count
                FROM member_events e
                JOIN channels c ON c.id = e.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE {where}
                GROUP BY c.id, c.name, s.name, s.platform
                ORDER BY count DESC, c.name
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        def _person_display(name: Any) -> str:
            text = "" if name is None else str(name)
            if (
                _normalized_history_name(text) == "you"
                and app.state.primary_person_name
            ):
                return app.state.primary_person_name
            return text

        return {
            "kind": kind,
            "by_actor": [
                {
                    "id": int(row[0]),
                    "display_name": _person_display(row[1]),
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in by_actor_rows
            ],
            "by_target": [
                {
                    "id": int(row[0]),
                    "display_name": _person_display(row[1]),
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in by_target_rows
            ],
            "by_chat": [
                {
                    "id": int(row[0]),
                    "name": _get_display_name(
                        row[1], row[2], app.state.fb_chat_names
                    ),
                    "source_name": row[2],
                    "platform": row[3],
                    "count": int(row[4]),
                }
                for row in by_chat_rows
            ],
        }
