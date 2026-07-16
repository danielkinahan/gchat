"""Shared media path, attachment, and reaction normalization helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlparse

from .moderation import media_url_if_allowed


def safe_child_path(root: Path, rel_path: str) -> Path | None:
    cleaned_parts = [
        part for part in Path(rel_path).parts if part not in {"", ".", ".."}
    ]
    if not cleaned_parts:
        return None
    target = (root / Path(*cleaned_parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def media_url(platform: str, source: str, rel_path: str) -> str:
    return f"/api/media?{urlencode({'platform': platform, 'source': source, 'path': rel_path})}"


def _media_url_for_file(
    platform: str, source: str, rel_path: str, file_path: Path
) -> str:
    return media_url_if_allowed(media_url(platform, source, rel_path), file_path)


def signal_source_root(data_dir: Path, source_folder: str) -> Path | None:
    if source_folder == "signal_decrypted":
        root = (data_dir / "signal_decrypted").resolve()
        if root.exists() and root.is_dir():
            return root
    decrypted_root = (data_dir / "signal_decrypted" / source_folder).resolve()
    if decrypted_root.exists() and decrypted_root.is_dir():
        return decrypted_root
    return None


def resolve_media_target(
    data_dir: Path, platform: str, source: str, path: str
) -> Path | None:
    if platform == "facebook":
        return safe_child_path((data_dir / "facebook" / source).resolve(), path)
    if platform == "signal":
        source_root = signal_source_root(data_dir, source)
        return safe_child_path(source_root, path) if source_root is not None else None
    source_root = (data_dir / "discord").resolve()
    target = safe_child_path(source_root, path)
    if target is not None and target.exists() and target.is_file():
        return target
    return safe_child_path((data_dir / "discord-media").resolve(), path)


def _normalize_facebook_preview_path(preview: str, source_folder: str) -> str:
    local_preview = preview
    parsed = urlparse(preview)
    if parsed.scheme in {"http", "https"} and parsed.netloc.startswith(
        ("localhost:", "127.0.0.1:")
    ):
        local_preview = parsed.path or ""
    local_preview = unquote(local_preview).strip()
    if local_preview.startswith("/"):
        local_preview = local_preview[1:]
    for prefix in ("messages/inbox/", "messages/archived_threads/"):
        if local_preview.startswith(prefix):
            remainder = local_preview[len(prefix) :]
            source_prefix = f"{source_folder}/"
            if remainder.startswith(source_prefix):
                return remainder[len(source_prefix) :]
    source_prefix = f"{source_folder}/"
    if local_preview.startswith(source_prefix):
        return local_preview[len(source_prefix) :]
    return local_preview


def _normalize_local_preview_path(preview: str) -> str:
    parsed = urlparse(preview)
    local_preview = preview
    if parsed.scheme in {"http", "https"} and parsed.netloc.startswith(
        ("localhost:", "127.0.0.1:")
    ):
        local_preview = parsed.path or ""
    local_preview = unquote(local_preview).strip()
    if local_preview.startswith("/"):
        local_preview = local_preview[1:]
    return local_preview


def build_signal_filename_index(data_dir: Path) -> dict[str, dict[str, str]]:
    decrypted_root = data_dir / "signal_decrypted"
    if not decrypted_root.exists() or not decrypted_root.is_dir():
        return {}
    index: dict[str, dict[str, str]] = {}
    for source_dir in decrypted_root.iterdir():
        if not source_dir.is_dir():
            continue
        media_root = source_dir / "media"
        if not media_root.exists() or not media_root.is_dir():
            continue
        source_map: dict[str, str] = {}
        for file_path in media_root.rglob("*"):
            if file_path.is_file():
                source_map.setdefault(
                    file_path.name.casefold(),
                    file_path.relative_to(source_dir).as_posix(),
                )
        if source_map:
            index[source_dir.name] = source_map
    return index


def resolve_local_attachment_url(
    attachment_preview: str | None,
    source_name: str,
    data_dir: Path,
    signal_filename_index: dict[str, dict[str, str]] | None = None,
) -> str | None:
    preview = (attachment_preview or "").strip()
    if not preview:
        return None
    if preview.casefold().startswith(("http://", "https://")):
        parsed = urlparse(preview)
        if parsed.netloc and not parsed.netloc.startswith(("localhost:", "127.0.0.1:")):
            return preview

    if source_name.startswith("Facebook: "):
        source_folder = source_name.removeprefix("Facebook: ").strip()
        source_root = (data_dir / "facebook" / source_folder).resolve()
        candidate = safe_child_path(
            source_root,
            _normalize_facebook_preview_path(preview, source_folder),
        )
        if candidate and candidate.exists() and candidate.is_file():
            relative = candidate.relative_to(source_root).as_posix()
            return _media_url_for_file("facebook", source_folder, relative, candidate)
        return None

    if source_name.startswith("Signal: "):
        source_folder = source_name.removeprefix("Signal: ").strip()
        source_root = signal_source_root(data_dir, source_folder)
        if source_root is None:
            return None
        normalized_preview = _normalize_local_preview_path(preview)
        direct = safe_child_path(source_root, normalized_preview)
        if direct and direct.exists() and direct.is_file():
            relative = direct.relative_to(source_root).as_posix()
            return _media_url_for_file("signal", source_folder, relative, direct)
        if signal_filename_index:
            mapped_rel_path = signal_filename_index.get(source_folder, {}).get(
                Path(normalized_preview).name.casefold()
            )
            if mapped_rel_path:
                mapped = safe_child_path(source_root, mapped_rel_path)
                if mapped and mapped.exists() and mapped.is_file():
                    return _media_url_for_file(
                        "signal", source_folder, mapped_rel_path, mapped
                    )
        return None

    if source_name.startswith("Discord: "):
        source_folder = source_name.removeprefix("Discord: ").strip()
        source_root = (data_dir / "discord").resolve()
        discord_media_root = (data_dir / "discord-media").resolve()
        parsed = urlparse(preview)
        normalized_preview = _normalize_local_preview_path(preview)
        absolute_candidate: Path | None = None
        if parsed.scheme == "file" and parsed.path:
            absolute_candidate = Path(unquote(parsed.path)).resolve()
        elif preview.startswith("/"):
            absolute_candidate = Path(unquote(preview)).resolve()
        if (
            absolute_candidate is not None
            and absolute_candidate.exists()
            and absolute_candidate.is_file()
        ):
            for root in (source_root, discord_media_root):
                try:
                    relative = absolute_candidate.relative_to(root).as_posix()
                    return _media_url_for_file(
                        "discord", source_folder, relative, absolute_candidate
                    )
                except ValueError:
                    continue
        direct = safe_child_path(source_root, normalized_preview)
        if direct and direct.exists() and direct.is_file():
            relative = direct.relative_to(source_root).as_posix()
            return _media_url_for_file("discord", source_folder, relative, direct)
        basename = Path(normalized_preview).name
        for fallback in (
            safe_child_path(source_root / "assets", basename),
            safe_child_path(source_root / "media", basename),
            safe_child_path(discord_media_root, basename),
        ):
            if fallback and fallback.exists() and fallback.is_file():
                try:
                    relative = fallback.relative_to(discord_media_root).as_posix()
                except ValueError:
                    relative = fallback.relative_to(source_root).as_posix()
                return _media_url_for_file("discord", source_folder, relative, fallback)
    return None


def _parse_json_array(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def normalize_reaction_details(
    value: object,
    source_name: str,
    data_dir: Path,
    signal_filename_index: dict[str, dict[str, str]] | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for detail in _parse_json_array(value):
        emoji_id = str(detail.get("emoji_id") or "").strip() or None
        image_url = str(detail.get("image_url") or "").strip()
        if source_name.startswith("Discord: ") and emoji_id is None:
            image_url = ""
        resolved_image_url = (
            resolve_local_attachment_url(
                image_url, source_name, data_dir, signal_filename_index
            )
            if image_url
            else None
        )
        normalized.append(
            {
                "name": str(detail.get("name") or "").strip(),
                "count": int(detail.get("count") or 0),
                "emoji_id": emoji_id,
                "image_url": resolved_image_url or image_url or None,
                "code": str(detail.get("code") or "").strip() or None,
                "is_animated": bool(detail.get("is_animated")),
            }
        )
    return normalized
