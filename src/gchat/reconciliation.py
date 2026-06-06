from __future__ import annotations

from dataclasses import dataclass
import os
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
    normalized_source_channel_to_theme: dict[tuple[str, str], str]
    platform_channel_to_theme: dict[tuple[str, str], str]
    normalized_channel_to_theme: dict[str, str]
    configured_theme_names: set[str]

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.strip().casefold().split())

    @staticmethod
    def _platform(source_name: str) -> str:
        if ":" not in source_name:
            return ""
        return source_name.split(":", 1)[0].strip().casefold()

    def resolve(self, source_name: str, channel_name: str) -> str:
        exact = self.channel_to_theme.get((source_name, channel_name))
        if exact is not None:
            return exact

        normalized_source = self._normalize(source_name)
        normalized_channel = self._normalize(channel_name)

        normalized_source_match = self.normalized_source_channel_to_theme.get((normalized_source, normalized_channel))
        if normalized_source_match is not None:
            return normalized_source_match

        platform = self._platform(source_name)
        platform_match = self.platform_channel_to_theme.get((platform, normalized_channel))
        if platform_match is not None:
            return platform_match

        channel_match = self.normalized_channel_to_theme.get(normalized_channel)
        if channel_match is not None:
            return channel_match

        # Some Facebook/Signal exports append opaque suffixes to source/channel names.
        # Accept prefix matches only when they resolve to exactly one configured theme.
        if platform in {"facebook", "signal"}:
            prefix_matches: set[str] = set()
            for (candidate_source, candidate_channel), theme_name in self.normalized_source_channel_to_theme.items():
                if self._platform(candidate_source) != platform:
                    continue
                if normalized_source.startswith(candidate_source) and normalized_channel.startswith(candidate_channel):
                    prefix_matches.add(theme_name)
            if len(prefix_matches) == 1:
                return next(iter(prefix_matches))

        return channel_name


@dataclass(frozen=True)
class ReconciliationConfig:
    people: PeopleConfig
    themes: ThemesConfig


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _default_config_dir(base_dir: Path | None = None) -> Path:
    env_dir = os.environ.get("GCHAT_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    if Path("/config").exists():
        return Path("/config")
    root = base_dir or Path.cwd()
    return root / "config"

def load_reconciliation(base_dir: Path | None = None, config_dir: Path | None = None) -> ReconciliationConfig:
    if config_dir is None:
        config_dir = _default_config_dir(base_dir)
    else:
        config_dir = Path(config_dir)
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
    normalized_source_channel_to_theme: dict[tuple[str, str], str] = {}
    configured_theme_names: set[str] = set()
    platform_channel_candidates: dict[tuple[str, str], set[str]] = {}
    normalized_channel_candidates: dict[str, set[str]] = {}
    if themes_path.exists():
        themes_data = _load_yaml(themes_path)
        for theme in themes_data.get("themes") or []:
            if not isinstance(theme, dict):
                continue
            theme_name = str(theme["name"])
            configured_theme_names.add(theme_name)
            for channel in theme.get("channels") or []:
                if not isinstance(channel, dict):
                    continue
                source_name = str(channel["source"])
                channel_name = str(channel["channel"])
                channel_to_theme[(source_name, channel_name)] = theme_name
                normalized_source = ThemesConfig._normalize(source_name)
                normalized_channel = ThemesConfig._normalize(channel_name)
                normalized_source_channel_to_theme[(normalized_source, normalized_channel)] = theme_name

                platform = ThemesConfig._platform(source_name)
                platform_key = (platform, normalized_channel)
                platform_channel_candidates.setdefault(platform_key, set()).add(theme_name)
                normalized_channel_candidates.setdefault(normalized_channel, set()).add(theme_name)

    platform_channel_to_theme = {
        key: next(iter(theme_names))
        for key, theme_names in platform_channel_candidates.items()
        if len(theme_names) == 1
    }
    normalized_channel_to_theme = {
        key: next(iter(theme_names))
        for key, theme_names in normalized_channel_candidates.items()
        if len(theme_names) == 1
    }

    return ReconciliationConfig(
        people=PeopleConfig(identity_to_person=identity_to_person),
        themes=ThemesConfig(
            channel_to_theme=channel_to_theme,
            normalized_source_channel_to_theme=normalized_source_channel_to_theme,
            platform_channel_to_theme=platform_channel_to_theme,
            normalized_channel_to_theme=normalized_channel_to_theme,
            configured_theme_names=configured_theme_names,
        ),
    )
