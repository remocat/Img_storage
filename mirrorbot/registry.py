"""Channel-name discovery: maps command name -> source channel ID."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)


class Registry:
    def __init__(self, suffix: str, reserved: Iterable[str]) -> None:
        self._suffix = suffix.lower()
        self._reserved = frozenset(reserved)
        self._by_name: dict[str, int] = {}
        self._by_id: dict[int, str] = {}

    def derive(self, channel_name: str) -> str | None:
        """'bae-dump' -> 'bae'. None if the channel doesn't match the pattern."""
        name = channel_name.lower()
        if not name.endswith(self._suffix) or len(name) <= len(self._suffix):
            return None
        return name[: -len(self._suffix)].strip("-_ ") or None

    def rebuild(self, guild: discord.Guild) -> None:
        by_name: dict[str, int] = {}
        for channel in guild.text_channels:  # already ordered by position
            name = self.derive(channel.name)
            if name is None:
                continue
            if name in self._reserved:
                log.warning("Channel %s skipped: command name %r is reserved", channel.id, name)
                continue
            if name in by_name:
                log.warning(
                    "Channel %s skipped: command %r already served by channel %s",
                    channel.id, name, by_name[name],
                )
                continue
            by_name[name] = channel.id
        self._by_name = by_name
        self._by_id = {cid: n for n, cid in by_name.items()}

    def find_channel(self, guild: discord.Guild, name: str) -> discord.TextChannel | None:
        """Same-named pool channel in any guild (used to locate the `set` destination)."""
        for channel in guild.text_channels:
            if self.derive(channel.name) == name:
                return channel
        return None

    def channel_id(self, name: str) -> int | None:
        return self._by_name.get(name)

    def has_channel(self, channel_id: int) -> bool:
        return channel_id in self._by_id

    def channel_ids(self) -> list[int]:
        return list(self._by_id)

    def items(self) -> list[tuple[str, int]]:
        return sorted(self._by_name.items())

    def __len__(self) -> int:
        return len(self._by_name)
