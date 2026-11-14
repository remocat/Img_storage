"""The bot: on_message prefix parser, capture sessions, pool upkeep."""
from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from dataclasses import dataclass

import discord

from .config import Config
from .media import is_supported_image, iter_attachments
from .pool import ImagePool
from .registry import Registry
from .store import ImageStore
from .views import ConfirmView, GalleryView

log = logging.getLogger(__name__)

RESERVED_NAMES = frozenset({"help", "cancel"})
_MB = 1024 * 1024


@dataclass(eq=False)
class CaptureSession:
    name: str
    prompt: discord.Message | None
    task: asyncio.Task | None = None


def _fit_lines(lines: list[str], limit: int = 3500) -> str:
    out: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit:
            out.append(f"…and {len(lines) - i} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


class MirrorBot(discord.Client):
    def __init__(self, cfg: Config) -> None:
        # Low-memory host: subscribe only to what we use (no typing/reactions/voice/etc.).
        intents = discord.Intents.none()
        intents.guilds = True            # channel discovery + channel events
        intents.guild_messages = True    # commands, live indexing, raw delete events
        intents.dm_messages = True       # commands in DMs
        intents.message_content = True   # privileged: read "!cmd" in guild channels
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            max_messages=None,                       # we use raw events, so skip the message cache
            chunk_guilds_at_startup=False,
            member_cache_flags=discord.MemberCacheFlags.none(),
        )
        self._upload_sem = asyncio.Semaphore(2)      # caps in-RAM image buffers (~2 x 8 MB x 2 copies)
        self.cfg = cfg
        self.exit_code = 0
        self.registry = Registry(cfg.suffix, RESERVED_NAMES)
        self.store = ImageStore(cfg.database_path)
        self.pool = ImagePool(self, self.store, cfg)
        self._sessions: dict[tuple[int, int], CaptureSession] = {}  # (user_id, channel_id)
        self._last_command: dict[int, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._ready_once = False

    # ---- lifecycle ------------------------------------------------------

    async def setup_hook(self) -> None:
        await self.store.open()

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            if session.task is not None:
                session.task.cancel()
        self._sessions.clear()
        for task in list(self._tasks):
            task.cancel()
        await super().close()
        await self.store.close()

    async def on_ready(self) -> None:
        if self._ready_once:  # on_ready can fire again after reconnects
            return
        source = self.get_guild(self.cfg.source_guild_id)
        target = self.get_guild(self.cfg.target_guild_id)
        missing = [
            gid
            for gid, guild in ((self.cfg.source_guild_id, source), (self.cfg.target_guild_id, target))
            if guild is None
        ]
        if source is None or target is None:
            log.critical(
                "Bot is not a member of guild(s) %s. Invite it to both guilds and restart.",
                sorted(set(missing)),
            )
            self.exit_code = 1
            await self.close()
            return
        self._ready_once = True
        self.registry.rebuild(source)
        log.info("Logged in as %s", self.user)
        log.info("Discovered %d command(s): %s", len(self.registry), ", ".join(n for n, _ in self.registry.items()))
        self._spawn(self._startup_backfill())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("Background task failed", exc_info=exc)

    async def _startup_backfill(self) -> None:
        total = 0
        for cid in self.registry.channel_ids():
            channel = self.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                total += await self.pool.backfill(channel)
        log.info("Startup backfill done: %d attachment(s) seen", total)

    # ---- registry upkeep (channel create/rename/delete in the source guild) ----

    async def _resync_registry(self) -> None:
        source = self.get_guild(self.cfg.source_guild_id)
        if source is None or not self._ready_once:
            return
        old = set(self.registry.channel_ids())
        self.registry.rebuild(source)
        new = set(self.registry.channel_ids())
        for cid in old - new:
            await self.store.remove_channel(cid)
        for cid in new - old:
            channel = self.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                self._spawn(self.pool.backfill(channel))

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id == self.cfg.source_guild_id:
            await self._resync_registry()

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id == self.cfg.source_guild_id:
            await self._resync_registry()

    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if after.guild.id == self.cfg.source_guild_id:
            await self._resync_registry()

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if self.registry.has_channel(payload.channel_id):
            await self.store.remove_messages([payload.message_id])

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if self.registry.has_channel(payload.channel_id):
            await self.store.remove_messages(payload.message_ids)

    # ---- small helpers --------------------------------------------------

    async def _send(self, channel: discord.abc.Messageable, **kwargs) -> discord.Message | None:
        try:
            return await channel.send(**kwargs)
        except discord.HTTPException as exc:
            log.warning("Could not send message (HTTP %s)", exc.status)
            return None

    async def _edit_quiet(self, message: discord.Message | None, **kwargs) -> None:
        if message is None:
            return
        try:
            await message.edit(**kwargs)
        except discord.HTTPException:
            pass

    async def _react_quiet(self, message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass

    def _cooldown_ok(self, user_id: int) -> bool:
        now = time.monotonic()
        last = self._last_command.get(user_id)
        if last is not None and now - last < self.cfg.cooldown_seconds:
            return False
        self._last_command[user_id] = now
        if len(self._last_command) > 10_000:
            self._last_command = {u: t for u, t in self._last_command.items() if now - t < 3600}
        return True

    def _upload_limit(self, guild: discord.Guild) -> int:
        return min(self.cfg.max_upload_bytes, guild.filesize_limit)

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)  # members intent is off, so usually a cache miss
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
        except discord.HTTPException as exc:
            log.warning("Member lookup failed (HTTP %s)", exc.status)
            return None

    async def _check_access(
        self, guild: discord.Guild, channel: discord.TextChannel, user_id: int, *, write: bool
    ) -> str | None:
        """None if allowed, otherwise a user-facing reason."""
        if not self.cfg.enforce_membership:
            return None
        member = await self._member(guild, user_id)
        if member is None:
            return f"You need to be a member of **{guild.name}** to do that."
        perms = channel.permissions_for(member)
        if not perms.view_channel:
            return "You don't have access to that channel."
        if write and not (perms.send_messages and perms.attach_files):
            return "You don't have permission to upload to the destination channel."
        return None

    # ---- message entry point -------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        try:
            await self._handle_message(message)
        except Exception:
            log.exception("Unhandled error while processing a message")

    async def _handle_message(self, message: discord.Message) -> None:
        # Live-index source pool channels (including our own confirmed uploads).
        if (
            message.guild is not None
            and message.guild.id == self.cfg.source_guild_id
            and self.registry.has_channel(message.channel.id)
        ):
            await self.pool.index_message(message)

        if message.author.bot:
            return

        prefix = self.cfg.prefix
        content = message.content.strip()
        is_command = content.startswith(prefix)
        key = (message.author.id, message.channel.id)
        session = self._sessions.get(key)

        if session is not None and not is_command:
            await self._handle_capture(message, key, session)
            return
        if is_command:
            await self._dispatch(message, content[len(prefix):])

    async def _dispatch(self, message: discord.Message, body: str) -> None:
        parts = body.split()
        if not parts:
            return
        name = parts[0].lower()
        action = parts[1].lower() if len(parts) > 1 else "get"
        p = self.cfg.prefix

        known = name in RESERVED_NAMES or self.registry.channel_id(name) is not None
        if not known:
            # In guilds stay silent: "!something" probably belongs to another bot.
            if message.guild is None:
                await self._send(message.channel, content=f"❓ Unknown command. Try `{p}help`.")
            return
        if not self._cooldown_ok(message.author.id):
            await self._react_quiet(message, "⏳")
            return

        if name == "help":
            await self._cmd_help(message)
        elif name == "cancel":
            await self._cmd_cancel(message)
        elif action == "get":
            await self._cmd_get(message, name)
        elif action == "set":
            await self._cmd_set(message, name)
        else:
            await self._send(message.channel, content=f"Usage: `{p}{name}` (or `{p}{name} get`) · `{p}{name} set`")

    # ---- help -----------------------------------------------------------

    async def _cmd_help(self, message: discord.Message) -> None:
        p = self.cfg.prefix
        source = self.get_guild(self.cfg.source_guild_id)
        target = self.get_guild(self.cfg.target_guild_id)
        if source is None or target is None:
            await self._send(message.channel, content="❌ I'm not connected to the configured servers.")
            return
        member = None
        if self.cfg.enforce_membership:
            member = await self._member(source, message.author.id)
            if member is None:
                await self._send(message.channel, content=f"You need to be a member of **{source.name}** to use these commands.")
                return

        counts = await self.store.counts()
        lines: list[str] = []
        for name, cid in self.registry.items():
            channel = source.get_channel(cid)
            if channel is None:
                continue
            if member is not None and not channel.permissions_for(member).view_channel:
                continue
            lines.append(f"`{p}{name}` → #{channel.name} · **{counts.get(cid, 0)}** image(s)")

        usage = (
            f"`{p}<name>` or `{p}<name> get` — browse images with ◀ ▶\n"
            f"`{p}<name> set` — add an image\n"
            f"`{p}cancel` — abort an upload in progress\n"
        )
        if source.id != target.id:
            usage += f"Uploads go to the matching channel in **{target.name}**.\n"
        embed = discord.Embed(
            title="Image mirror — commands",
            description=usage + "\n" + (_fit_lines(lines) or "_No commands discovered._"),
            color=discord.Color.blurple(),
        )
        await self._send(message.channel, embed=embed)

    # ---- get ------------------------------------------------------------

    async def _cmd_get(self, message: discord.Message, name: str) -> None:
        p = self.cfg.prefix
        source = self.get_guild(self.cfg.source_guild_id)
        cid = self.registry.channel_id(name)
        channel = self.get_channel(cid) if cid is not None else None
        if source is None or not isinstance(channel, discord.TextChannel):
            await self._send(message.channel, content="❌ The channel behind this command no longer exists.")
            return
        if reason := await self._check_access(source, channel, message.author.id, write=False):
            await self._send(message.channel, content=f"❌ {reason}")
            return

        refs = await self.store.refs(channel.id, self.cfg.pool_snapshot_limit)
        if not refs:
            await self.pool.refresh_if_empty(channel)
            refs = await self.store.refs(channel.id, self.cfg.pool_snapshot_limit)
        if not refs:
            await self._send(message.channel, content=f"📭 **{name}** has no images yet. Add one with `{p}{name} set`.")
            return

        start = len(refs) - 1 if self.cfg.get_strategy == "newest" else random.randrange(len(refs))
        view = GalleryView(
            pool=self.pool,
            name=name,
            channel_name=channel.name,
            refs=refs,
            start_index=start,
            owner_id=message.author.id,
            timeout=self.cfg.view_timeout,
        )
        embed = await view.load_current()
        if embed is None:
            await self._send(message.channel, content="⚠️ Couldn't load an image right now. Try again in a moment.")
            return
        view.message = await self._send(message.channel, embed=embed, view=view)

    # ---- set (capture session -> preview -> confirm -> commit) ------------

    async def _cmd_set(self, message: discord.Message, name: str) -> None:
        p = self.cfg.prefix
        target_guild = self.get_guild(self.cfg.target_guild_id)
        if target_guild is None:
            await self._send(message.channel, content="❌ The destination server is unavailable.")
            return
        target = self.registry.find_channel(target_guild, name)
        if target is None:
            await self._send(
                message.channel,
                content=f"❌ **{target_guild.name}** has no `{name}{self.cfg.suffix}` channel to receive images.",
            )
            return
        if reason := await self._check_access(target_guild, target, message.author.id, write=True):
            await self._send(message.channel, content=f"❌ {reason}")
            return
        me = target_guild.me
        if me is None or not all(
            getattr(target.permissions_for(me), attr) for attr in ("view_channel", "send_messages", "attach_files")
        ):
            await self._send(message.channel, content="❌ I don't have permission to post in the destination channel.")
            return

        key = (message.author.id, message.channel.id)
        replaced = self._pop_session(key)
        if replaced is not None:
            await self._edit_quiet(replaced.prompt, content="🚫 Replaced by a newer upload request.")

        secs = int(self.cfg.session_timeout)
        prompt = await self._send(
            message.channel,
            content=f"📥 Send the image for **{name}** in this chat within **{secs}s**. Type `{p}cancel` to abort.",
        )
        if prompt is None:
            return
        session = CaptureSession(name=name, prompt=prompt)
        session.task = asyncio.create_task(self._expire(key, session))
        self._sessions[key] = session

    def _pop_session(self, key: tuple[int, int]) -> CaptureSession | None:
        session = self._sessions.pop(key, None)
        if session is not None and session.task is not None:
            session.task.cancel()
        return session

    async def _expire(self, key: tuple[int, int], session: CaptureSession) -> None:
        try:
            await asyncio.sleep(self.cfg.session_timeout)
        except asyncio.CancelledError:
            return
        if self._sessions.get(key) is not session:
            return
        del self._sessions[key]
        await self._edit_quiet(
            session.prompt,
            content=(
                f"⌛ Upload session expired after {int(self.cfg.session_timeout)}s. "
                f"Run `{self.cfg.prefix}{session.name} set` again to retry."
            ),
        )

    async def _cmd_cancel(self, message: discord.Message) -> None:
        session = self._pop_session((message.author.id, message.channel.id))
        if session is None:
            await self._send(message.channel, content="Nothing to cancel.")
            return
        await self._edit_quiet(session.prompt, content="🚫 Upload cancelled.")
        await self._send(message.channel, content="🚫 Cancelled.")

    async def _handle_capture(self, message: discord.Message, key: tuple[int, int], session: CaptureSession) -> None:
        attachments = list(iter_attachments(message))
        if not attachments:
            return  # ordinary chatter while a session is open: ignore silently
        chosen = next((a for a in attachments if is_supported_image(a)), None)
        if chosen is None:
            await self._send(message.channel, content=f"❌ I only accept PNG, JPG, GIF or WebP. Send one of those, or `{self.cfg.prefix}cancel`.")
            return
        target_guild = self.get_guild(self.cfg.target_guild_id)
        target = self.registry.find_channel(target_guild, session.name) if target_guild else None
        if target_guild is None or target is None:
            self._pop_session(key)
            await self._send(message.channel, content="❌ The destination channel no longer exists.")
            return
        limit = self._upload_limit(target_guild)
        if chosen.size > limit:
            await self._send(
                message.channel,
                content=f"❌ That file is {chosen.size / _MB:.1f} MB; the limit is {limit / _MB:.0f} MB. Send a smaller one.",
            )
            return

        # No awaits between the session lookup in _handle_message and here, so a
        # second upload can't capture the same session twice.
        self._pop_session(key)
        await self._edit_quiet(session.prompt, content="📎 Image received. Confirm below.")

        author = message.author
        name = session.name

        async def commit() -> str:
            return await self._commit_upload(name, chosen, author, limit)

        view = ConfirmView(owner_id=author.id, timeout=self.cfg.confirm_timeout, commit=commit)
        embed = discord.Embed(
            title="Add this image?",
            description=f"Command: `{self.cfg.prefix}{name}`\nDestination: **#{target.name}** in **{target_guild.name}**",
            color=discord.Color.gold(),
        )
        embed.set_image(url=chosen.url)
        embed.set_footer(text=f"Only you can confirm · expires in {int(self.cfg.confirm_timeout)}s")
        view.message = await self._send(message.channel, embed=embed, view=view)

    async def _commit_upload(self, name: str, attachment: discord.Attachment, author: discord.abc.User, limit: int) -> str:
        guild = self.get_guild(self.cfg.target_guild_id)
        if guild is None:
            return "❌ The destination server is unavailable."
        target = self.registry.find_channel(guild, name)
        if target is None:
            return "❌ The destination channel no longer exists."
        if reason := await self._check_access(guild, target, author.id, write=True):
            return f"❌ {reason}"
        me = guild.me
        if me is None or not all(
            getattr(target.permissions_for(me), attr) for attr in ("view_channel", "send_messages", "attach_files")
        ):
            return "❌ I don't have permission to post in the destination channel."

        async with self._upload_sem:  # at most 2 images buffered in RAM at once
            try:
                data = await attachment.read()
            except discord.NotFound:
                return "❌ Your original upload was deleted before you confirmed. Please send it again."
            except discord.HTTPException:
                return "❌ I couldn't download the image. Please try again."
            if len(data) > limit:
                return "❌ The image is larger than the upload limit."

            try:
                sent = await target.send(
                    content=f"📥 Added via bot by <@{author.id}> (`{author.id}`)",
                    file=discord.File(io.BytesIO(data), filename=attachment.filename),
                )
            except discord.Forbidden:
                return "❌ I'm not allowed to post in the destination channel."
            except discord.HTTPException:
                return "❌ Discord rejected the upload (file too large or a temporary error)."
            finally:
                del data  # release the buffer before indexing

        if self.registry.has_channel(target.id):
            await self.pool.index_message(sent)  # immediate; on_message would also catch it
        return f"✅ Added to **{name}**."
