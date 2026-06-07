from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

_SPACE_RE = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _SPACE_RE.sub(" ", text).strip()


def fix_facebook_mojibake(text: str) -> str:
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def stable_color(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    hue = int(digest[:8], 16) / 0xFFFFFFFF
    saturation = 0.62
    lightness = 0.54
    return hsl_to_hex(hue * 360.0, saturation, lightness)


def hsl_to_hex(h: float, s: float, l: float) -> str:
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    values = [round((component + m) * 255) for component in (r, g, b)]
    return "#" + "".join(f"{value:02X}" for value in values)


def message_counts(content: str) -> tuple[int, int]:
    normalized = normalize_whitespace(content)
    if not normalized:
        return 0, 0
    return len(normalized.split(" ")), len(normalized)


def to_utc_naive(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def parse_iso_datetime(value: str) -> datetime:
    return to_utc_naive(datetime.fromisoformat(value))


def hash_message(parts: list[str]) -> str:
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest
