"""Message context and browsing routes."""

from __future__ import annotations

import json
from html import escape

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from ..media_utils import media_url, resolve_local_attachment_url


def _connect(app: FastAPI) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(app.state.db_path), read_only=True)


def _display_name(
    channel_name: str, source_name: str, fb_chat_names: dict[str, str]
) -> str:
    if source_name.startswith("Facebook: "):
        display_name = fb_chat_names.get(channel_name)
        if display_name:
            return display_name
    return channel_name


def register_message_routes(app: FastAPI) -> None:
    @app.get("/api/message-context")
    def message_context(message_id: str) -> dict[str, str | None]:
        """Return a best-effort URL to view a message in its original export."""
        with _connect(app) as con:
            row = con.execute(
                """
                SELECT m.id, c.platform_channel_id, s.platform, s.name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        _msg_id, channel_raw_id, platform, source_name = row
        data_dir = app.state.data_dir
        if platform == "discord":
            root = (data_dir / "discord").resolve()
            if root.exists():
                for path in root.rglob(f"{channel_raw_id}.html"):
                    return {
                        "url": media_url(
                            "discord",
                            source_name.removeprefix("Discord: ").strip(),
                            path.relative_to(root).as_posix(),
                        ),
                        "fragment": f"chatlog__message-container-{message_id}",
                    }
            raise HTTPException(
                status_code=404, detail="HTML export not found for message"
            )
        if platform == "signal":
            root = (data_dir / "signal_decrypted").resolve()
            if root.exists():
                for source_dir in root.iterdir():
                    candidate = source_dir / f"{channel_raw_id}.html"
                    if candidate.exists():
                        return {
                            "url": media_url("signal", source_dir.name, candidate.name),
                            "fragment": f"chatlog__message-container-{message_id}",
                        }
            raise HTTPException(
                status_code=404, detail="Signal HTML export not found for message"
            )
        if platform == "facebook":
            root = (data_dir / "facebook" / channel_raw_id).resolve()
            if root.exists() and root.is_dir():
                for path in root.iterdir():
                    if path.suffix == ".html":
                        return {
                            "url": media_url("facebook", channel_raw_id, path.name),
                            "fragment": None,
                        }
            raise HTTPException(
                status_code=404, detail="Facebook HTML export not found for message"
            )
        raise HTTPException(
            status_code=404, detail="Unsupported platform for message context"
        )

    @app.get("/api/message-snippet")
    def message_snippet(
        message_id: str, context: int = Query(default=5, ge=0, le=50)
    ) -> Response:
        """Return a self-contained HTML window around a message."""
        with _connect(app) as con:
            row = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview,
                       m.attachment_count, m.reaction_count, m.reaction_summary,
                       m.reaction_details_json, m.channel_id,
                       c.platform_channel_id, s.platform, s.name,
                       p.display_name, p.color
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Message not found")
            (
                msg_id,
                msg_ts,
                msg_content,
                msg_attach_preview,
                msg_attach_count,
                msg_reaction_count,
                msg_reaction_summary,
                msg_reaction_details,
                msg_channel_id,
                _platform_channel_id,
                _platform,
                source_name,
                person_name,
                person_color,
            ) = row
            channel_rows = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview,
                       m.attachment_count, m.reaction_count, m.reaction_summary,
                       m.reaction_details_json, p.display_name, p.color
                FROM messages m
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.channel_id = ?
                ORDER BY m.ts ASC, m.id ASC
                """,
                [msg_channel_id],
            ).fetchall()
            index = next(
                (
                    index
                    for index, channel_row in enumerate(channel_rows)
                    if str(channel_row[0]) == str(msg_id)
                ),
                None,
            )
            if index is None:
                window_rows = [
                    (
                        msg_id,
                        msg_ts,
                        msg_content,
                        msg_attach_preview,
                        msg_attach_count,
                        msg_reaction_count,
                        msg_reaction_summary,
                        msg_reaction_details,
                        person_name,
                        person_color,
                    )
                ]
            else:
                window_rows = channel_rows[
                    max(0, index - context) : min(
                        len(channel_rows), index + context + 1
                    )
                ]

        def attachment_html(preview: str | None) -> str:
            try:
                url = resolve_local_attachment_url(
                    preview,
                    source_name,
                    app.state.data_dir,
                    app.state.signal_filename_index,
                )
            except Exception:
                url = None
            if not url:
                return ""
            if any(
                url.lower().endswith(extension)
                for extension in (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                return (
                    f'<div class="attachment"><img src="{url}" alt="attachment"/></div>'
                )
            return (
                f'<div class="attachment"><a href="{url}" target="_blank" '
                'rel="noreferrer">Attachment</a></div>'
            )

        parts = [
            '<!doctype html><html><head><meta charset="utf-8"/>'
            '<meta name="viewport" content="width=device-width,initial-scale=1"/>',
            "<style>body{font-family:Inter,system-ui,Roboto,Arial,sans-serif;padding:18px;background:#0f172a;color:#e2e8f0} .snippet{max-width:900px;margin:0 auto} .message{padding:8px;border-radius:8px;margin-bottom:8px;background:rgba(255,255,255,0.02)} .message.target{background:linear-gradient(90deg,#0ea5e9, #7dd3fc);color:#04111a} .meta{font-size:0.9rem;color:#94a3b8} .content{margin-top:6px;white-space:pre-wrap} .attachment img{max-width:100%;height:auto;border-radius:6px;margin-top:6px}</style>",
            '</head><body><div class="snippet">',
            f'<div class="snippet-meta">Showing {len(window_rows)} messages in context</div>',
            '<div style="display:none">'
            + escape(str([item[0] for item in window_rows]))
            + "</div>",
        ]
        for item in window_rows:
            (
                item_id,
                item_ts,
                item_content,
                item_attach_preview,
                _item_attach_count,
                _item_reaction_count,
                item_reaction_summary,
                item_reaction_details,
                item_person_name,
                item_person_color,
            ) = item
            reactions_html = ""
            try:
                if item_reaction_details:
                    pills = []
                    for reaction in json.loads(item_reaction_details):
                        name = escape(str(reaction.get("name") or ""))
                        count = int(reaction.get("count") or 0)
                        if name:
                            pills.append(
                                f'<span class="react-pill">{name}×{count}</span>'
                            )
                    if pills:
                        reactions_html = (
                            f'<div class="reactions">{" ".join(pills)}</div>'
                        )
                elif item_reaction_summary:
                    reactions_html = (
                        f'<div class="reactions">'
                        f"{escape(str(item_reaction_summary))}</div>"
                    )
            except Exception:
                reactions_html = ""
            css_class = "message target" if item_id == msg_id else "message"
            parts.append(
                f'<div id="chatlog__message-container-{item_id}" class="{css_class}">'
                f'<div class="meta"><strong style="color:'
                f'{escape(str(item_person_color or "#fff"))}">'
                f"{escape(str(item_person_name or 'Unknown'))}</strong> "
                f"<time>{escape(str(item_ts) if item_ts is not None else 'N/A')}</time>"
                f'</div><div class="content">{escape(str(item_content or ""))}</div>'
                f"{attachment_html(item_attach_preview)}{reactions_html}</div>"
            )
        parts.append("</div></body></html>")
        return Response(content="\n".join(parts), media_type="text/html")

    @app.get("/api/message-window")
    def message_window(
        message_id: str,
        context: int = Query(default=10, ge=0, le=500),
        full: bool = False,
    ) -> dict[str, object]:
        """Return a JSON window of messages around the given message id."""
        with _connect(app) as con:
            row = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview,
                       m.attachment_count, m.reaction_count, m.reaction_summary,
                       m.reaction_details_json, m.channel_id,
                       c.platform_channel_id, s.id, s.platform, s.name,
                       p.display_name, p.color, c.name, m.reply_to_id
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Message not found")
            (
                msg_id,
                msg_ts,
                msg_content,
                msg_attach_preview,
                msg_attach_count,
                msg_reaction_count,
                msg_reaction_summary,
                msg_reaction_details,
                msg_channel_id,
                platform_channel_id,
                source_id,
                platform,
                source_name,
                person_name,
                person_color,
                channel_initial_name,
                msg_reply_to_id,
            ) = row
            total_in_channel = int(
                con.execute(
                    "SELECT COUNT(*) FROM messages WHERE channel_id = ?",
                    [msg_channel_id],
                ).fetchone()[0]
            )
            target_pos_row = con.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE channel_id = ?
                  AND (ts < ? OR (ts = ? AND id < ?))
                """,
                [msg_channel_id, msg_ts, msg_ts, msg_id],
            ).fetchone()
            if target_pos_row is None or total_in_channel == 0:
                selected = [
                    (
                        msg_id,
                        msg_ts,
                        msg_content,
                        msg_attach_preview,
                        msg_attach_count,
                        msg_reaction_count,
                        msg_reaction_summary,
                        msg_reaction_details,
                        person_name,
                        person_color,
                        None,
                        None,
                        False,
                        msg_reply_to_id,
                    )
                ]
            else:
                index = int(target_pos_row[0])
                if full:
                    fetch_offset, fetch_limit = 0, total_in_channel
                else:
                    fetch_offset = max(0, index - context)
                    fetch_end = min(total_in_channel, index + context + 1)
                    fetch_limit = fetch_end - fetch_offset
                selected = con.execute(
                    f"""
                    SELECT m.id, m.ts, m.content, m.attachment_preview,
                           m.attachment_count, m.reaction_count,
                           m.reaction_summary, m.reaction_details_json,
                           p.display_name, p.color, m.person_id,
                           (
                               SELECT pnc.new_name
                               FROM person_name_changes pnc
                               WHERE pnc.person_id = m.person_id
                                 AND pnc.source_id = ?
                                 AND json_extract_string(
                                     pnc.payload_json, '$.chatId'
                                 ) = ?
                                 AND pnc.ts <= m.ts
                               ORDER BY pnc.ts DESC
                               LIMIT 1
                           ) AS nickname_at_time,
                           {"m.is_system" if app.state.has_is_system else "FALSE"}
                               AS is_system,
                           m.reply_to_id
                    FROM messages m
                    LEFT JOIN people p ON p.id = m.person_id
                    WHERE m.channel_id = ?
                    ORDER BY m.ts ASC, m.id ASC
                    LIMIT ? OFFSET ?
                    """,
                    [
                        source_id,
                        platform_channel_id,
                        msg_channel_id,
                        fetch_limit,
                        fetch_offset,
                    ],
                ).fetchall()
            channel_name_row = con.execute(
                """
                SELECT new_name FROM channel_name_changes
                WHERE channel_id = ? AND ts <= ?
                ORDER BY ts DESC LIMIT 1
                """,
                [msg_channel_id, msg_ts],
            ).fetchone()
            channel_name_at_time = _display_name(
                (
                    channel_name_row[0]
                    if channel_name_row
                    else (channel_initial_name or source_name)
                ),
                source_name,
                app.state.fb_chat_names,
            )

        from ..display_config import people_display_metadata

        avatar_by_name = {
            name: metadata["avatar"]
            for name, metadata in people_display_metadata(app.state.config_dir).items()
            if metadata.get("avatar")
        }
        items: list[dict[str, object]] = []
        for selected_row in selected:
            (
                item_id,
                item_ts,
                item_content,
                item_attach_preview,
                item_attach_count,
                item_reaction_count,
                item_reaction_summary,
                item_reaction_details,
                item_person_name,
                item_person_color,
                _item_person_id,
                item_nickname,
                item_is_system,
                item_reply_to_id,
            ) = selected_row
            attachment_url = (
                resolve_local_attachment_url(
                    item_attach_preview,
                    source_name,
                    app.state.data_dir,
                    app.state.signal_filename_index,
                )
                if item_attach_preview
                else None
            )
            content_starts_with_canonical = bool(
                item_nickname
                and item_person_name
                and item_content
                and item_content.casefold().startswith(
                    item_person_name.casefold() + " "
                )
            )
            resolved_name = (
                item_person_name
                if content_starts_with_canonical
                else (item_nickname or item_person_name)
            )
            items.append(
                {
                    "id": item_id,
                    "ts": item_ts.isoformat() if item_ts else None,
                    "content": item_content,
                    "attachment_preview": item_attach_preview,
                    "attachment_url": attachment_url,
                    "attachment_count": item_attach_count,
                    "reaction_count": item_reaction_count,
                    "reaction_summary": item_reaction_summary,
                    "reaction_details": (
                        json.loads(item_reaction_details)
                        if item_reaction_details
                        else None
                    ),
                    "person_name": resolved_name,
                    "person_name_canonical": item_person_name,
                    "person_color": item_person_color,
                    "avatar_url": avatar_by_name.get(item_person_name or "", "")
                    or None,
                    "is_system": bool(item_is_system),
                    "reply_to_id": item_reply_to_id,
                    "channel_id": msg_channel_id,
                    "platform": platform,
                    "source_name": source_name,
                }
            )
        return {
            "channel_name": channel_name_at_time,
            "platform": platform,
            "source_name": source_name,
            "items": items,
            "total_in_channel": total_in_channel,
            "is_full": full or (len(items) >= total_in_channel),
        }
