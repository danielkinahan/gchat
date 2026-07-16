"""Validate all configuration files and return a concise diagnostic summary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .display_config import configured_people_names, configured_theme_names
from .moderation import load_moderation_config
from .reconciliation import load_reconciliation


def validate_configuration(config_dir: Path) -> dict[str, Any]:
    reconciliation = load_reconciliation(config_dir=config_dir)
    moderation = load_moderation_config(config_dir)
    people = configured_people_names(config_dir)
    themes = configured_theme_names(config_dir)
    return {
        "valid": True,
        "people": len(people),
        "themes": len(themes),
        "identities": len(reconciliation.people.identity_to_person),
        "bots": len(reconciliation.people.bot_person_names),
        "excluded_messages": len(moderation.excluded_message_ids),
        "blocked_media_hashes": len(moderation.blocked_media_sha256),
        "blocked_media_filenames": len(moderation.blocked_media_filenames),
    }
