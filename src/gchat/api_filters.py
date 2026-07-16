"""Shared parsing and SQL generation for dashboard query filters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import HTTPException

_theme_channel_ids: dict[str, list[int]] = {}


@dataclass(frozen=True)
class QueryFilters:
    start: date | None
    end: date | None
    people: list[int]
    themes: list[int]
    platforms: list[str]
    include_bots: bool = False


def set_theme_channel_ids(value: dict[str, list[int]]) -> None:
    global _theme_channel_ids
    _theme_channel_ids = value


def csv_ints(value: str | None, field: str) -> list[int]:
    if not value:
        return []
    items: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            items.append(int(item))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid {field} filter: {item!r}"
            ) from exc
    return items


def csv_strings(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def filters_clause(
    filters: QueryFilters,
    params: list[Any],
    reconciliation: Any | None = None,
    theme_id_to_name: dict[int, str] | None = None,
    theme_to_channel_ids: dict[str, list[int]] | None = None,
) -> str:
    clauses = ["1 = 1"]
    if filters.start is not None:
        clauses.append("m.ts >= ?")
        params.append(datetime.combine(filters.start, time.min))
    if filters.end is not None:
        clauses.append("m.ts < ?")
        params.append(datetime.combine(filters.end + timedelta(days=1), time.min))
    if filters.people:
        placeholders = ", ".join("?" for _ in filters.people)
        clauses.append(f"m.person_id IN ({placeholders})")
        params.extend(filters.people)
    bot_names = (
        reconciliation.people.bot_person_names
        if reconciliation is not None and not filters.include_bots
        else frozenset()
    )
    if bot_names:
        placeholders = ", ".join("?" for _ in bot_names)
        clauses.append(
            f"m.person_id NOT IN ("
            f"SELECT id FROM people WHERE display_name IN ({placeholders})"
            f")"
        )
        params.extend(sorted(bot_names))
    if filters.themes:
        if reconciliation is None or theme_id_to_name is None:
            placeholders = ", ".join("?" for _ in filters.themes)
            clauses.append(f"c.theme_id IN ({placeholders})")
            params.extend(filters.themes)
        else:
            selected_theme_names = {
                theme_id_to_name.get(theme_id) for theme_id in filters.themes
            }
            selected_theme_names.discard(None)
            channel_index = (
                theme_to_channel_ids
                if theme_to_channel_ids is not None
                else _theme_channel_ids
            )
            if not selected_theme_names:
                clauses.append("1 = 0")
            elif channel_index:
                channel_ids = sorted(
                    {
                        channel_id
                        for theme_name in selected_theme_names
                        for channel_id in channel_index.get(theme_name, [])
                    }
                )
                if not channel_ids:
                    clauses.append("1 = 0")
                else:
                    placeholders = ", ".join("?" for _ in channel_ids)
                    clauses.append(f"c.id IN ({placeholders})")
                    params.extend(channel_ids)
            else:
                exact_terms: list[str] = []
                exact_params: list[Any] = []
                fallback_terms: list[str] = []
                fallback_params: list[Any] = []
                for (
                    source_name,
                    channel_name,
                ), theme_name in reconciliation.themes.channel_to_theme.items():
                    if theme_name not in selected_theme_names:
                        continue
                    exact_terms.append("(s.name = ? AND c.name = ?)")
                    exact_params.extend([source_name, channel_name])
                    if source_name.startswith("Facebook: "):
                        fallback_terms.append(
                            "("
                            "s.platform = 'facebook' "
                            "AND starts_with(lower(s.name), lower(? || '_')) "
                            "AND starts_with(lower(c.name), lower(? || '_'))"
                            ")"
                        )
                        fallback_params.extend([source_name, channel_name])
                    elif source_name.startswith("Signal: "):
                        fallback_terms.append(
                            "("
                            "s.platform = 'signal' "
                            "AND starts_with(lower(s.name), lower(?)) "
                            "AND lower(c.name) = lower(?)"
                            ")"
                        )
                        fallback_params.extend([source_name, channel_name])
                theme_terms: list[str] = []
                if exact_terms:
                    theme_terms.append(f"({' OR '.join(exact_terms)})")
                if fallback_terms:
                    theme_terms.append(f"({' OR '.join(fallback_terms)})")
                if theme_terms:
                    clauses.append(f"({' OR '.join(theme_terms)})")
                    params.extend(exact_params)
                    params.extend(fallback_params)
                else:
                    clauses.append("1 = 0")
    if filters.platforms:
        placeholders = ", ".join("?" for _ in filters.platforms)
        clauses.append(f"s.platform IN ({placeholders})")
        params.extend(filters.platforms)
    return " AND ".join(clauses)
