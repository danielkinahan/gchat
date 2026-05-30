from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PeopleConfig:
    identity_to_person: dict[tuple[str, str], tuple[str, str]]

    def resolve(self, platform: str, raw_id: str, fallback_name: str) -> tuple[str, str]:
        return self.identity_to_person.get((platform, raw_id), (fallback_name, ""))


@dataclass(frozen=True)
class ThemesConfig:
    channel_to_theme: dict[tuple[str, str], str]

    def resolve(self, source_name: str, channel_name: str) -> str:
        return self.channel_to_theme.get((source_name, channel_name), channel_name)


@dataclass(frozen=True)
class ReconciliationConfig:
    people: PeopleConfig
    themes: ThemesConfig


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def load_reconciliation(base_dir: Path | None = None) -> ReconciliationConfig:
    root = base_dir or Path.cwd()
    config_dir = root / "config"
    people_path = config_dir / "people.yaml"
    themes_path = config_dir / "themes.yaml"
    if not people_path.exists():
        people_path = config_dir / "people.example.yaml"
    if not themes_path.exists():
        themes_path = config_dir / "themes.example.yaml"

    identity_to_person: dict[tuple[str, str], tuple[str, str]] = {}
    if people_path.exists():
        people_data = _load_yaml(people_path)
        for person in people_data.get("people", []):
            name = str(person["name"])
            color = str(person.get("color") or "")
            for identity in person.get("identities", []):
                platform = str(identity["platform"]).lower()
                raw_id = str(identity.get("id") or identity.get("username") or identity.get("name"))
                identity_to_person[(platform, raw_id)] = (name, color or name)

    channel_to_theme: dict[tuple[str, str], str] = {}
    if themes_path.exists():
        themes_data = _load_yaml(themes_path)
        for theme in themes_data.get("themes") or []:
            if not isinstance(theme, dict):
                continue
            theme_name = str(theme["name"])
            for channel in theme.get("channels") or []:
                if not isinstance(channel, dict):
                    continue
                source_name = str(channel["source"])
                channel_name = str(channel["channel"])
                channel_to_theme[(source_name, channel_name)] = theme_name

    return ReconciliationConfig(
        people=PeopleConfig(identity_to_person=identity_to_person),
        themes=ThemesConfig(channel_to_theme=channel_to_theme),
    )
