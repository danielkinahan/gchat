from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PersonSeed:
    platform: str
    raw_id: str
    display_name: str


@dataclass(frozen=True)
class SourceSeed:
    platform: str
    name: str


@dataclass(frozen=True)
class ChannelSeed:
    source_name: str
    raw_id: str
    name: str
    theme_name: str


@dataclass(frozen=True)
class MessageSeed:
    id: str
    source_name: str
    channel_raw_id: str
    channel_name: str
    theme_name: str
    person: PersonSeed
    ts: datetime
    content: str
    reply_to_id: str | None = None
    attachment_count: int = 0
    reaction_count: int = 0


@dataclass(frozen=True)
class NameChangeSeed:
    source_name: str
    platform: str
    entity_kind: str
    entity_raw_id: str
    previous_name: str | None
    new_name: str
    ts: datetime
    kind: str
    payload_json: str | None = None
