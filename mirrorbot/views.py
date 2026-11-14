"""Button UIs: image browser (◀ ▶) and upload confirmation."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

import discord

from .pool import ImagePool
from .store import ImageRef

log = logging.getLogger(__name__)

_MAX_RESOLVE_ATTEMPTS = 5  # bounds REST calls when several images were deleted


class _OwnerOnlyView(discord.ui.View):
    """Only the user who ran the command may press the buttons."""

    def __init__(self, *, owner_id: int, timeout: float) -> None:
        super().__init__(timeout=timeout)
        self._owner_id = owner_id
        self.message: discord.Message | None = None  # set by the caller after sending

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who ran the command can use these buttons.", ephemeral=True
        )
        return False

    async def _edit_message_quiet(self, **kwargs) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            pass


class GalleryView(_OwnerOnlyView):
    def __init__(
        self,
        *,
        pool: ImagePool,
        name: str,
        channel_name: str,
        refs: list[ImageRef],
        start_index: int,
        owner_id: int,
        timeout: float,
    ) -> None:
        super().__init__(owner_id=owner_id, timeout=timeout)
        self._pool = pool
        self._name = name
        self._channel_name = channel_name
        self._refs = list(refs)  # snapshot, ordered oldest -> newest
        self._index = start_index

    def _embed(self, url: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"{self._name.capitalize()} gallery",
            description=f"Image **{self._index + 1}** of **{len(self._refs)}** · #{self._channel_name}",
            color=discord.Color.blurple(),
        )
        embed.set_image(url=url)
        embed.set_footer(text="◀ older · newer ▶ · loops around")
        return embed

    async def load_current(self, step: int = 1) -> discord.Embed | None:
        """Show the image at the current index; skip dead ones in direction `step`."""
        attempts = 0
        while self._refs and attempts < _MAX_RESOLVE_ATTEMPTS:
            self._index %= len(self._refs)
            url = await self._pool.resolve(self._refs[self._index])
            if url is not None:
                return self._embed(url)
            del self._refs[self._index]  # dead or unreachable: drop from this session
            if step < 0:
                self._index -= 1
            attempts += 1
        return None

    async def _navigate(self, interaction: discord.Interaction, step: int) -> None:
        await interaction.response.defer()  # resolving may take >3s of REST calls
        if self._refs:
            self._index = (self._index + step) % len(self._refs)
        embed = await self.load_current(step)
        if embed is None:
            self.stop()
            await interaction.edit_original_response(
                content="⚠️ Couldn't load any images right now. Try the command again.",
                embed=None,
                view=None,
            )
            return
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.primary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._navigate(interaction, -1)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._navigate(interaction, 1)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await self._edit_message_quiet(view=self)


class ConfirmView(_OwnerOnlyView):
    """Confirm / Cancel. `commit` performs the write and returns a status message."""

    def __init__(
        self,
        *,
        owner_id: int,
        timeout: float,
        commit: Callable[[], Awaitable[str]],
    ) -> None:
        super().__init__(owner_id=owner_id, timeout=timeout)
        self._commit = commit

    @discord.ui.button(label="Confirm", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()  # one shot: also prevents double-clicks and the timeout path
        await interaction.response.edit_message(content="⏳ Uploading…", embed=None, view=None)
        try:
            result = await self._commit()
        except Exception:
            log.exception("Upload commit failed")
            result = "❌ Unexpected error while uploading. Check the destination channel before retrying."
        await interaction.edit_original_response(content=result)

    @discord.ui.button(label="Cancel", emoji="🚫", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="🚫 Cancelled. Nothing was uploaded.", embed=None, view=None
        )

    async def on_timeout(self) -> None:
        await self._edit_message_quiet(
            content="⌛ Confirmation expired. Nothing was uploaded.", embed=None, view=None
        )
