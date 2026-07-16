"""Load moderation rules: excluded messages and blocked media."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .config_models import ModerationFile

REMOVED_MEDIA_URL = "/api/media-removed"

REMOVED_MEDIA_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="480" height="240" viewBox="0 0 480 240" role="img" aria-label="This image has been removed from gChat">
  <rect width="480" height="240" fill="#111827"/>
  <rect x="1" y="1" width="478" height="238" fill="none" stroke="#334155" stroke-width="2" rx="12"/>
  <text x="240" y="118" fill="#94a3b8" font-family="Inter, ui-sans-serif, system-ui, sans-serif" font-size="15" text-anchor="middle">This image has been removed from gChat</text>
</svg>
""".encode("utf-8")


@dataclass(frozen=True)
class ModerationConfig:
    excluded_message_ids: frozenset[str]
    blocked_media_sha256: frozenset[str]
    blocked_media_filenames: frozenset[str]


_EMPTY = ModerationConfig(frozenset(), frozenset(), frozenset())
_active: ModerationConfig | None = None


def _normalize_sha256(value: str) -> str:
    return value.strip().lower()


def _parse_message_ids(data: object) -> frozenset[str]:
    if not isinstance(data, list):
        return frozenset()
    return frozenset(str(item).strip() for item in data if item and str(item).strip())


def _parse_sha256_list(data: object) -> frozenset[str]:
    if not isinstance(data, list):
        return frozenset()
    return frozenset(
        normalized
        for item in data
        if item and (normalized := _normalize_sha256(str(item)))
    )


def _parse_filename_list(data: object) -> frozenset[str]:
    if not isinstance(data, list):
        return frozenset()
    return frozenset(
        Path(str(item)).name.casefold()
        for item in data
        if item and Path(str(item)).name
    )


def load_moderation_config(config_dir: Path) -> ModerationConfig:
    """Load moderation.yaml, falling back to legacy excluded_messages.yaml."""
    moderation_path = config_dir / "moderation.yaml"
    if moderation_path.exists():
        data = ModerationFile.model_validate(
            yaml.safe_load(moderation_path.read_text(encoding="utf-8")) or {}
        ).model_dump()
        blocked = data.get("blocked_media")
        blocked_dict = blocked if isinstance(blocked, dict) else {}
        return ModerationConfig(
            excluded_message_ids=_parse_message_ids(data.get("excluded_message_ids")),
            blocked_media_sha256=_parse_sha256_list(blocked_dict.get("sha256")),
            blocked_media_filenames=_parse_filename_list(blocked_dict.get("filenames")),
        )

    legacy_path = config_dir / "excluded_messages.yaml"
    if legacy_path.exists():
        try:
            data = yaml.safe_load(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            return _EMPTY
        return ModerationConfig(
            excluded_message_ids=_parse_message_ids(data),
            blocked_media_sha256=frozenset(),
            blocked_media_filenames=frozenset(),
        )

    return _EMPTY


def set_active_moderation(config: ModerationConfig) -> None:
    global _active
    _active = config
    file_sha256.cache_clear()


def get_active_moderation() -> ModerationConfig:
    if _active is None:
        return _EMPTY
    return _active


@lru_cache(maxsize=4096)
def file_sha256(path: str, mtime_ns: int, size: int) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_blocked_media_file(
    path: Path,
    config: ModerationConfig | None = None,
) -> bool:
    cfg = config or get_active_moderation()
    if not cfg.blocked_media_sha256 and not cfg.blocked_media_filenames:
        return False
    if not path.is_file():
        return False
    if (
        cfg.blocked_media_filenames
        and path.name.casefold() in cfg.blocked_media_filenames
    ):
        return True
    if cfg.blocked_media_sha256:
        stat = path.stat()
        digest = file_sha256(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        if digest in cfg.blocked_media_sha256:
            return True
    return False


def media_url_if_allowed(local_url: str | None, file_path: Path | None) -> str | None:
    """Return removed-media placeholder URL when the resolved file is blocked."""
    if not local_url:
        return None
    if file_path is not None and is_blocked_media_file(file_path):
        return REMOVED_MEDIA_URL
    return local_url
