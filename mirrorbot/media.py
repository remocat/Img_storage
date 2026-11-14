"""Helpers for deciding what counts as an image attachment."""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import discord

ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
ALLOWED_MIME = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def iter_attachments(message: discord.Message) -> Iterator[discord.Attachment]:
    """Yield direct attachments plus those inside forwarded-message snapshots."""
    yield from message.attachments
    for snapshot in getattr(message, "message_snapshots", None) or ():
        yield from getattr(snapshot, "attachments", ())


def is_supported_image(attachment: discord.Attachment) -> bool:
    """True for png/jpg/gif/webp. Extension must match; MIME must match if Discord gave one."""
    if not attachment.filename.lower().endswith(ALLOWED_EXTENSIONS):
        return False
    mime = (attachment.content_type or "").split(";")[0].strip().lower()
    return not mime or mime in ALLOWED_MIME
