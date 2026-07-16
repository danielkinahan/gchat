"""Media serving, hashing, and anchored-export routes."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from ..media_utils import media_url, resolve_media_target
from ..moderation import (
    REMOVED_MEDIA_SVG,
    file_sha256,
    is_blocked_media_file,
)


def _connect(app: FastAPI):  # type: ignore[no-untyped-def]
    import duckdb

    return duckdb.connect(str(app.state.db_path), read_only=True)


def register_media_routes(app: FastAPI) -> None:
    @app.get("/api/media-removed")
    def media_removed() -> Response:
        return Response(content=REMOVED_MEDIA_SVG, media_type="image/svg+xml")

    @app.get("/api/media-hash")
    def media_hash(platform: str, source: str, path: str) -> dict[str, str]:
        if platform not in {"facebook", "signal", "discord"}:
            raise HTTPException(status_code=404, detail="Unsupported media platform")
        target = resolve_media_target(app.state.data_dir, platform, source, path)
        if target is None or not target.is_file():
            raise HTTPException(status_code=404, detail="Media file not found")
        stat = target.stat()
        digest = file_sha256(str(target.resolve()), stat.st_mtime_ns, stat.st_size)
        return {"sha256": digest, "path": path}

    @app.get("/api/media", response_model=None)
    def media_file(platform: str, source: str, path: str) -> FileResponse | Response:
        if platform not in {"facebook", "signal", "discord"}:
            raise HTTPException(status_code=404, detail="Unsupported media platform")
        target = resolve_media_target(app.state.data_dir, platform, source, path)
        if target is None or not target.is_file():
            raise HTTPException(status_code=404, detail="Media file not found")
        if is_blocked_media_file(target):
            return Response(content=REMOVED_MEDIA_SVG, media_type="image/svg+xml")
        return FileResponse(target)

    @app.get("/api/media-anchored")
    def media_anchored(message_id: str) -> Response:
        """Serve an HTML export with an injected anchor id for the message."""
        with _connect(app) as con:
            row = con.execute(
                """
                SELECT m.content, c.platform_channel_id, s.platform, s.name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        content, channel_raw_id, platform, source_name = row
        snippet = " ".join(str(content or "").split())[:200].strip()
        data_dir = app.state.data_dir

        def find_file_and_root() -> tuple[Path, Path] | tuple[None, None]:
            if platform == "discord":
                root = (data_dir / "discord").resolve()
                if root.exists():
                    for path in root.rglob(f"{channel_raw_id}.html"):
                        return path, root
                return None, None
            if platform == "signal":
                root = (data_dir / "signal_decrypted").resolve()
                if not root.exists():
                    return None, None
                for source_dir in root.iterdir():
                    candidate = source_dir / f"{channel_raw_id}.html"
                    if candidate.exists():
                        return candidate, source_dir
                for path in root.rglob("*.html"):
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    if snippet and snippet in " ".join(text.split()):
                        return path, path.parent
                return None, None
            if platform == "facebook":
                root = (data_dir / "facebook" / channel_raw_id).resolve()
                if root.exists() and root.is_dir():
                    for path in root.iterdir():
                        if path.suffix == ".html":
                            text = path.read_text(encoding="utf-8", errors="ignore")
                            if snippet and snippet in " ".join(text.split()):
                                return path, root
                    for path in root.iterdir():
                        if path.suffix == ".html":
                            return path, root
                all_root = (data_dir / "facebook").resolve()
                if all_root.exists():
                    for path in all_root.rglob("*.html"):
                        try:
                            text = path.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            continue
                        if snippet and snippet in " ".join(text.split()):
                            return path, path.parent
            return None, None

        file_path, source_root = find_file_and_root()
        if file_path is None or source_root is None:
            raise HTTPException(
                status_code=404, detail="HTML export not found for message"
            )
        try:
            html = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raise HTTPException(
                status_code=404, detail="Failed to read HTML export"
            ) from None
        soup = BeautifulSoup(html, "html.parser")

        try:
            source_folder = None
            if platform == "signal":
                signal_root = (data_dir / "signal_decrypted").resolve()
                try:
                    parts = file_path.relative_to(signal_root).parts
                    if parts:
                        source_folder = parts[0]
                except Exception:
                    source_folder = source_root.name
            elif platform == "facebook":
                source_folder = channel_raw_id
            elif platform == "discord":
                source_folder = source_name.removeprefix("Discord: ").strip()
            if source_folder:
                for tag in soup.find_all(True):
                    for attr in ("src", "href"):
                        value = tag.get(attr)
                        if not value or not isinstance(value, str):
                            continue
                        value = value.strip()
                        if not value or value.startswith(
                            ("data:", "http://", "https://")
                        ):
                            continue
                        candidate = None
                        try:
                            candidate_path = (
                                file_path.parent / unquote(value)
                            ).resolve()
                            if candidate_path.exists() and candidate_path.is_file():
                                candidate = candidate_path
                        except Exception:
                            pass
                        if candidate is None:
                            try:
                                relative = Path(unquote(value)).as_posix().lstrip("/")
                                candidate = source_root / relative
                                if not candidate.exists():
                                    candidate = None
                            except Exception:
                                candidate = None
                        if candidate is None:
                            continue
                        try:
                            relative = candidate.relative_to(source_root).as_posix()
                        except Exception:
                            relative = candidate.name
                        tag[attr] = media_url(platform, source_folder, relative)
        except Exception:
            pass

        target_id = f"chatlog__message-container-{message_id}"
        found = None
        if snippet:
            normalized_snippet = snippet.casefold()
            for tag in soup.find_all(True):
                try:
                    text = " ".join(tag.get_text(" ", strip=True).split())
                except Exception:
                    continue
                if normalized_snippet in text.casefold():
                    found = tag
                    break
        if found is None:
            for class_name in (
                "chatlog__message-container",
                "pam",
                "message",
                "chat-message",
            ):
                found = soup.select_one(f".{class_name}")
                if found:
                    break
        if found is not None:
            parent = found
            for _ in range(3):
                if parent.name and parent.name.lower() in {"div", "article", "li"}:
                    break
                if parent.parent is None:
                    break
                parent = parent.parent
            parent["id"] = target_id
        return Response(content=str(soup), media_type="text/html")
