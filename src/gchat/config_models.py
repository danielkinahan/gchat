"""Typed schemas for user-managed YAML configuration files."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityConfig(StrictConfigModel):
    platform: str = Field(min_length=1)
    id: str | int | None = None
    username: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def require_identifier(self) -> IdentityConfig:
        if self.id is None and not self.username and not self.name:
            raise ValueError("one of id, username, or name is required")
        return self


class PersonConfig(StrictConfigModel):
    name: str = Field(min_length=1)
    color: str | None = None
    avatar: str | None = None
    is_bot: bool = False
    identities: list[IdentityConfig] = Field(default_factory=list)


class PeopleFile(StrictConfigModel):
    people: list[PersonConfig] = Field(default_factory=list)


class ThemeChannelConfig(StrictConfigModel):
    source: str = Field(min_length=1)
    channel: str = Field(min_length=1)


class ThemeConfig(StrictConfigModel):
    name: str = Field(min_length=1)
    emoji: str | None = None
    channels: list[ThemeChannelConfig] | None = Field(default_factory=list)


class ThemesFile(StrictConfigModel):
    themes: list[ThemeConfig] = Field(default_factory=list)


class BlockedMediaConfig(StrictConfigModel):
    sha256: list[str] = Field(default_factory=list)
    filenames: list[str] = Field(default_factory=list)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, values: list[str]) -> list[str]:
        for value in values:
            normalized = value.strip().casefold()
            if len(normalized) != 64 or any(
                char not in "0123456789abcdef" for char in normalized
            ):
                raise ValueError(f"invalid SHA-256 digest: {value!r}")
        return values


class ModerationFile(StrictConfigModel):
    excluded_message_ids: list[str] = Field(default_factory=list)
    blocked_media: BlockedMediaConfig = Field(default_factory=BlockedMediaConfig)
