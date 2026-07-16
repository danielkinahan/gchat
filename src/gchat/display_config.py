"""Typed accessors for dashboard display configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from .config_models import PeopleFile, ThemesFile


def _people_file(config_dir: Path) -> PeopleFile:
    path = config_dir / "people.yaml"
    if not path.exists():
        return PeopleFile()
    return PeopleFile.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    )


def _themes_file(config_dir: Path) -> ThemesFile:
    path = config_dir / "themes.yaml"
    if not path.exists():
        return ThemesFile()
    return ThemesFile.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    )


def configured_people_names(config_dir: Path) -> set[str]:
    return {person.name for person in _people_file(config_dir).people}


def configured_theme_names(config_dir: Path) -> list[str]:
    return [theme.name for theme in _themes_file(config_dir).themes]


def people_display_metadata(config_dir: Path) -> dict[str, dict[str, str]]:
    return {
        person.name: {
            "color": person.color or "",
            "avatar": person.avatar or "",
        }
        for person in _people_file(config_dir).people
    }


def theme_emoji(config_dir: Path) -> dict[str, str]:
    return {
        theme.name: theme.emoji
        for theme in _themes_file(config_dir).themes
        if theme.emoji
    }


def primary_person_name(config_dir: Path) -> str | None:
    people = _people_file(config_dir).people
    return people[0].name if people else None
