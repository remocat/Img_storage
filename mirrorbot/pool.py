"""Pool maintenance: bounded backfill, live indexing, fresh-URL resolution."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import discord

from .media import is_supported_image, iter_attachments
from .store import ImageRef, ImageStore

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

_CACHE_MAX = 2048
_LAZY_BACKFILL_COOLDOWN = 60.0  # seconds between on-demand rescans of one channel


class ImagePool:
    def __init__(self, client: discord.Client, store: ImageStore, cfg: Config) -> None:
        self._client = client
        self._store = store
        self._cfg = cfg
        self._urls: dict[int, tuple[float, str]] = {}  # attachment_id -> (expires_at, url)
        self._last_backfill: dict[int, float] = {}

    # ---- indexing -------------------------------------------------------

    @staticmethod
    def _refs_for(message: discord.Message) -> list[ImageRef]:
        return [
            ImageRef(a.id, message.channel.id, message.id)
            for a in iter_attachments(message)
            if is_supported_image(a)
        ]

    async def index_message(self, message: discord.Message) -> None:
        await self._store.add_many(self._refs_for(message))

    async def backfill(self, channel: discord.TextChannel) -> int:
        """Read at most `backfill_limit` recent messages. Never unbounded."""
        self._last_backfill[channel.id] = time.monotonic()
        refs: list[ImageRef] = []
        try:
            async for message in channel.history(limit=self._cfg.backfill_limit):
                refs.extend(self._refs_for(message))
        except discord.Forbidden:
            log.warning("Missing Read Message History in channel %s", channel.id)
        except discord.HTTPException as exc:
            log.warning("History fetch failed for channel %s (HTTP %s)", channel.id, exc.status)
        await self._store.add_many(refs)
        return len(refs)

    async def refresh_if_empty(self, channel: discord.TextChannel) -> int:
        """On-demand rescan for an empty pool, rate-limited per channel."""
        last = self._last_backfill.get(channel.id, float("-inf"))
        if time.monotonic() - last < _LAZY_BACKFILL_COOLDOWN:
            return 0
        return await self.backfill(channel)

    # ---- resolving ------------------------------------------------------

    def _cache_get(self, attachment_id: int) -> str | None:
        item = self._urls.get(attachment_id)
        if item is None:
            return None
        if item[0] > time.monotonic():
            return item[1]
        del self._urls[attachment_id]
        return None

    def _cache_put(self, attachment_id: int, url: str) -> None:
        if self._cfg.url_cache_seconds <= 0:
            return
        if len(self._urls) >= _CACHE_MAX:
            now = time.monotonic()
            self._urls = {k: v for k, v in self._urls.items() if v[0] > now}
            if len(self._urls) >= _CACHE_MAX:
                self._urls.clear()
        self._urls[attachment_id] = (time.monotonic() + self._cfg.url_cache_seconds, url)

    async def resolve(self, ref: ImageRef) -> str | None:
        """Return a fresh CDN URL, or None if the image can't be shown.

        Discord CDN URLs are signed and expire, so we never store them: we
        re-fetch the message. Deleted messages/attachments are pruned from the
        index; transient errors are not (the image may be fine next time).
        """
        cached = self._cache_get(ref.attachment_id)
        if cached is not None:
            return cached
        channel = self._client.get_channel(ref.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            message = await channel.fetch_message(ref.message_id)
        except discord.NotFound:
            await self._store.remove_messages([ref.message_id])
            return None
        except discord.HTTPException as exc:  # includes Forbidden
            log.warning("Could not fetch message %s (HTTP %s)", ref.message_id, exc.status)
            return None
        for attachment in iter_attachments(message):
            if attachment.id == ref.attachment_id:
                self._cache_put(ref.attachment_id, attachment.url)
                return attachment.url
        await self._store.remove_attachment(ref.attachment_id)  # edited out of the message
        return None
