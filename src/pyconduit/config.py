"""Configuration schema and loader.

Config is layered: values come from a YAML file, and any field can be overridden
by an environment variable prefixed ``PYCONDUIT_`` using ``__`` as the nesting
delimiter (e.g. ``PYCONDUIT_SERVER__PORT=9000``). Environment always wins, which
keeps secrets and ops toggles out of the checked-in YAML.

This module is pure (no framework/slixmpp imports) so it is trivially testable.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Holds the parsed YAML for the config currently being constructed. Set by
# load_config() immediately before instantiating Config; consumed by the YAML
# settings source below. Construction is synchronous, so this is safe.
_yaml_data: dict[str, Any] = {}


class _YamlSource(PydanticBaseSettingsSource):
    """A settings source that yields values parsed from the YAML file.

    Registered below env sources so environment variables override YAML.
    """

    def get_field_value(self, field, field_name):  # required abstract method
        return _yaml_data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(_yaml_data)


class AuthMode(StrEnum):
    proxy = "proxy"
    dev = "dev"


class AuditFormat(StrEnum):
    text = "text"
    json = "json"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    brand_title: str = "PyConduit"


class AuthConfig(BaseModel):
    mode: AuthMode = AuthMode.proxy
    header: str = "X-Remote-User"
    dev_default_user: str = "alice"
    users_file: Path = Path("users.json")
    credentials_file: Path = Path("credentials.json")


class ReconnectConfig(BaseModel):
    initial_seconds: float = 1.0
    max_seconds: float = 60.0
    factor: float = 2.0


class XmppConfig(BaseModel):
    host: str = "localhost"
    port: int = 5222
    tls: bool = False
    verify_certs: bool = False
    idle_timeout_seconds: float = 300.0
    reconnect: ReconnectConfig = Field(default_factory=ReconnectConfig)


class MucConfig(BaseModel):
    discovery_servers: list[str] = Field(default_factory=lambda: ["conference.example.com"])


class HistoryConfig(BaseModel):
    page_size: int = 50


class UiConfig(BaseModel):
    sound_enabled_default: bool = True


class AuditDestination(BaseModel):
    type: str = "stdout"  # stdout | file
    path: Path | None = None
    format: AuditFormat = AuditFormat.text


class AuditConfig(BaseModel):
    destinations: list[AuditDestination] = Field(
        default_factory=lambda: [AuditDestination()]
    )


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PYCONDUIT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    xmpp: XmppConfig = Field(default_factory=XmppConfig)
    muc: MucConfig = Field(default_factory=MucConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Priority (first wins): explicit init args > env vars > YAML file > defaults.
        return (init_settings, env_settings, dotenv_settings, _YamlSource(settings_cls))


def load_config(path: str | Path | None = None) -> Config:
    """Load config from a YAML file, with env-var overrides on top.

    A missing or empty file yields an all-defaults config (still overridable by env).
    """
    global _yaml_data
    data: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
    _yaml_data = data
    try:
        return Config()
    finally:
        _yaml_data = {}
