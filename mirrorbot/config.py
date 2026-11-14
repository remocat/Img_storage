"""Configuration loading and validation. Never prints or logs the token."""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ConfigError(Exception):
    """Raised for any invalid or missing configuration value."""


_PLACEHOLDER_TOKENS = frozenset({"", "ENTER BOT TOKEN", "<BOT TOKEN>", "YOUR_BOT_TOKEN"})
_STRATEGIES = frozenset({"random", "newest"})
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class Config:
    token: str = field(repr=False)  # repr=False: never leaks via logging a Config
    source_guild_id: int
    target_guild_id: int
    suffix: str
    prefix: str
    get_strategy: str
    backfill_limit: int
    pool_snapshot_limit: int
    session_timeout: float
    confirm_timeout: float
    view_timeout: float
    url_cache_seconds: float
    cooldown_seconds: float
    max_upload_bytes: int
    enforce_membership: bool
    database_path: Path
    log_level: str


def _number(
    cp: configparser.ConfigParser,
    section: str,
    key: str,
    default: Any,
    cast: Callable[[str], Any],
    lo: float,
    hi: float,
) -> Any:
    raw = cp.get(section, key, fallback=None)
    if raw is None or not raw.strip():
        if default is None:
            raise ConfigError(f"Missing required setting: [{section}] {key}")
        return default
    try:
        value = cast(raw.strip())
    except ValueError:
        raise ConfigError(f"[{section}] {key} must be a number, got {raw.strip()!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"[{section}] {key} must be between {lo} and {hi}, got {value}")
    return value


def _boolean(cp: configparser.ConfigParser, section: str, key: str, default: bool) -> bool:
    raw = cp.get(section, key, fallback=None)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"[{section}] {key} must be true or false, got {raw.strip()!r}")


def load_config(path: Path) -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    if not cp.read(path, encoding="utf-8"):
        raise ConfigError(f"Config file not found: {path} (copy config.ini.example to config.ini)")

    token = os.environ.get("DISCORD_TOKEN") or cp.get("discord", "token", fallback="")
    token = token.strip().strip("\"'").strip()
    if token.upper() in _PLACEHOLDER_TOKENS:
        raise ConfigError("No bot token configured: set [discord] token or the DISCORD_TOKEN env var")

    suffix = cp.get("commands", "suffix", fallback="-dump").strip().lower()
    prefix = cp.get("commands", "prefix", fallback="!").strip()
    if not suffix or any(c.isspace() for c in suffix):
        raise ConfigError("[commands] suffix must be non-empty and contain no whitespace")
    if not prefix or any(c.isspace() for c in prefix):
        raise ConfigError("[commands] prefix must be non-empty and contain no whitespace")

    strategy = cp.get("behavior", "get_strategy", fallback="random").strip().lower()
    if strategy not in _STRATEGIES:
        raise ConfigError(f"[behavior] get_strategy must be one of {sorted(_STRATEGIES)}, got {strategy!r}")

    level = cp.get("logging", "level", fallback="INFO").strip().upper()
    if level not in _LOG_LEVELS:
        raise ConfigError(f"[logging] level must be one of {sorted(_LOG_LEVELS)}, got {level!r}")

    db_path = Path(cp.get("storage", "database_path", fallback="data/index.db").strip())
    if not db_path.is_absolute():
        db_path = Path(path).resolve().parent / db_path

    return Config(
        token=token,
        source_guild_id=_number(cp, "discord", "source_guild_id", None, int, 1, 2**63 - 1),
        target_guild_id=_number(cp, "discord", "target_guild_id", None, int, 1, 2**63 - 1),
        suffix=suffix,
        prefix=prefix,
        get_strategy=strategy,
        backfill_limit=_number(cp, "behavior", "backfill_limit", 200, int, 1, 1000),
        pool_snapshot_limit=_number(cp, "behavior", "pool_snapshot_limit", 2000, int, 1, 20000),
        session_timeout=_number(cp, "behavior", "session_timeout_seconds", 90.0, float, 10, 600),
        confirm_timeout=_number(cp, "behavior", "confirm_timeout_seconds", 60.0, float, 10, 600),
        view_timeout=_number(cp, "behavior", "view_timeout_seconds", 300.0, float, 30, 900),
        url_cache_seconds=_number(cp, "behavior", "url_cache_seconds", 600.0, float, 0, 3600),
        cooldown_seconds=_number(cp, "behavior", "cooldown_seconds", 2.0, float, 0, 60),
        max_upload_bytes=int(_number(cp, "behavior", "max_upload_mb", 8.0, float, 1, 100) * 1024 * 1024),
        enforce_membership=_boolean(cp, "security", "enforce_membership", True),
        database_path=db_path,
        log_level=level,
    )
