
import discord
import asyncio
from discord.ext import commands
from gallery import Gallery


class GalleryView(discord.ui.View):
    def __init__(self, gallery):
        super().__init__(timeout=300)
        self.gallery = gallery
        self.index = 0

    async def refresh(self, interaction: discord.Interaction):
        image_count = len(self.gallery.posts["images"])

        if image_count == 0:
            await interaction.response.send_message(
                "⚠️ No images available in this gallery.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="✨ Character Gallery Dashboard ✨",
            description=f"Browsing image file **{self.index + 1}** of **{image_count}**",
            color=discord.Color.blurple()
        )

        embed.set_image(
            url=self.gallery.posts["images"][self.index]
        )

        embed.set_footer(
            text="Navigate smoothly using control inputs • Loops automatically"
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self
        )

    @discord.ui.button(
        label="◀ Previous",
        style=discord.ButtonStyle.primary
    )
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        image_count = len(self.gallery.posts["images"])

        if image_count == 0:
            await interaction.response.send_message(
                "⚠️ No images available.",
                ephemeral=True
            )
            return

        self.index = (self.index - 1) % image_count

        await self.refresh(interaction)

    @discord.ui.button(
        label="Next ▶",
        style=discord.ButtonStyle.primary
    )
    async def next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        image_count = len(self.gallery.posts["images"])

        if image_count == 0:
            await interaction.response.send_message(
                "⚠️ No images available.",
                ephemeral=True
            )
            return

        self.index = (self.index + 1) % image_count

        await self.refresh(interaction)


class ViewerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_uploads = set()

        self.channel_map = {
            "hakos": "hakos-dump",
            "bae": "hakos-dump",
            "ceci": "ceci-dump",
            "cecelia": "ceci-dump",
            "mumei": "mumei-dump",
            "fauna": "fauna-dump",
            "irys": "irys-dump",
            "kronii": "kronii-dump"
        }

    def get_channel_name(self, text: str) -> str:
        cleaned = text.lower().strip()
        return self.channel_map.get(cleaned, "")

    async def run_upload_wizard(self, message):
        """Conversational step-by-step wizard uploader framework inside user DMs."""

        author = message.author
        dm_channel = message.channel

        def check_text(m):
            return (
                m.author.id == author.id
                and isinstance(m.channel, discord.DMChannel)
            )

        # STEP 1: KEYWORD SPECIFICATION REQUEST
        valid_options = ", ".join(
            f"`{k}`" for k in sorted(set(self.channel_map.keys()))
        )

        await dm_channel.send(
            f"📥 **[Step 1/2]** Which gallery would you like to save to?\n"
            f"Please type one of the valid options:\n{valid_options}"
        )

        try:
            name_msg = await self.bot.wait_for(
                "message",
                check=check_text,
                timeout=60.0
            )
        except asyncio.TimeoutError:
            await dm_channel.send(
                "❌ Upload session timed out due to inactivity."
            )
            return

        target_channel_name = self.get_channel_name(name_msg.content)

        if not target_channel_name:
            await dm_channel.send(
                f"❌ `{name_msg.content}` is not a recognized gallery name. "
                "Session canceled."
            )
            return

        # STEP 2: ATTACHMENT / SNAPSHOT MEDIA COLLECTION REQUEST
        await dm_channel.send(
            "Now, please **upload or forward** the image(s) "
            "directly into this chat thread."
        )

        try:
            image_msg = await self.bot.wait_for(
                "message",
                check=check_text,
                timeout=90.0
            )
        except asyncio.TimeoutError:
            await dm_channel.send(
                "❌ Upload session timed out waiting for media files."
            )
            return

        # LOCATE SERVER CHANNEL
        server_channel = None

        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if channel.name == target_channel_name:
                    server_channel = channel
                    break

            if server_channel:
                break

        if not server_channel:
            await dm_channel.send(
                f"❌ Configuration Error: Couldn't locate "
                f"`#{target_channel_name}` on the server sidebar."
            )
            return

        # STEP 3: CONVERT ATTACHMENTS TO DISCORD FILES
        processing_msg = await dm_channel.send(
            "⏳ Processing and forwarding media streams to server clouds..."
        )

        files_to_send = []

        if image_msg.attachments:
            for attach in image_msg.attachments:
                try:
                    file_obj = await attach.to_file()
                    files_to_send.append(file_obj)
                except discord.HTTPException:
                    pass

        # PROCESS FORWARDED MESSAGE SNAPSHOTS
        if (
            hasattr(image_msg, "message_snapshots")
            and image_msg.message_snapshots
        ):
            for snapshot in image_msg.message_snapshots:
                if hasattr(snapshot, "attachments"):
                    for snap_attach in snapshot.attachments:
                        try:
                            file_obj = await snap_attach.to_file()
                            files_to_send.append(file_obj)
                        except discord.HTTPException:
                            pass

        # FORWARD FILES TO SERVER CHANNEL
        if files_to_send:
            try:
                await server_channel.send(
                    content=(
                        f"📥 Remote upload contribution from user "
                        f"**{author.name}**"
                    ),
                    files=files_to_send
                )

                await processing_msg.delete()

                success_embed = discord.Embed(
                    title="🎉 Upload Success!",
                    description=(
                        f"Successfully forwarded **{len(files_to_send)}** "
                        f"image files straight into  "
                        f"**** cloud storage vault channel!"
                    ),
                    color=discord.Color.green()
                )

                await dm_channel.send(embed=success_embed)

            except discord.Forbidden:
                await processing_msg.delete()

                await dm_channel.send(
                    f"❌ Access Denied: The bot lacks permissions "
                    f"to write inside your `#{target_channel_name}` channel!"
                )

        else:
            await processing_msg.delete()

            if "http://" in image_msg.content or "https://" in image_msg.content:
                await server_channel.send(
                    content=(
                        f"📥 Remote link contribution from user "
                        f"**{author.name}**:\n{image_msg.content}"
                    )
                )

                await dm_channel.send(
                    "🎉 Link forwarded successfully straight to "
                    "server channel timeline streams!"
                )

            else:
                await dm_channel.send(
                    "❌ Error: No valid image files, snapshots, "
                    "or links were detected in your message package. "
                    "Session closed."
                )

    @commands.Cog.listener()
    async def on_message(self, message):

        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):

            # Bypasses regular command processing if this user is in the wizard steps
            if message.author.id in self.active_uploads:
                return

            command = message.content.lower().strip()

            # UPLOAD WIZARD
            if command in ["set", "upload"]:
                self.active_uploads.add(message.author.id)

                try:
                    await self.run_upload_wizard(message)
                finally:
                    self.active_uploads.discard(message.author.id)

                return

            # GALLERY VIEWER
            target_channel_name = self.get_channel_name(command)

            if target_channel_name:

                target_channel = None

                for guild in self.bot.guilds:
                    for channel in guild.text_channels:
                        if channel.name == target_channel_name:
                            target_channel = channel
                            break

                    if target_channel:
                        break

                if not target_channel:
                    await message.channel.send(
                        f"⚠️ Error: I couldn't find a channel named "
                        f"#{target_channel_name} inside the server profiles."
                    )
                    return

                loading_msg = await message.channel.send(
                    "🔄 Fetching live image attachments directly "
                    "from server dump channels..."
                )

                gallery = Gallery()

                try:
                    await gallery.load_gallery(target_channel)

                except discord.Forbidden:
                    await loading_msg.delete()

                    await message.channel.send(
                        f"⚠️ Permission Lock: Make sure I have "
                        f"'Read Message History' enabled inside "
                        f"#{target_channel_name} settings!"
                    )
                    return

                await loading_msg.delete()

                if not gallery.posts or not gallery.posts["images"]:
                    await message.channel.send(
                        f"⚠️ Error: There are no valid image attachments "
                        f"inside your #{target_channel_name} channel yet."
                    )
                    return

                image_count = len(gallery.posts["images"])

                embed = discord.Embed(
                    title=f"✨ {command.capitalize()} Gallery Stream ✨",
                    description=(
                        f"Browsing image file 1 of {image_count}"
                    ),
                    color=discord.Color.blurple()
                )

                embed.set_image(
                    url=gallery.posts["images"][0]
                )

                embed.set_footer(
                    text="Use arrows below to navigate • Loops automatically"
                )

                await message.channel.send(
                    embed=embed,
                    view=GalleryView(gallery)
                )

            # UNKNOWN COMMAND
            else:
                embed = discord.Embed(
                    title="❌ Unknown Request",
                    description=(
                        "• Use set or upload to save your images.\n"
                        "• Type a character name below to view their gallery:\n"
                        "  bae, cecelia, ceci, fauna, hakos, irys, kronii, mumei"
                    ),
                    color=discord.Color.red()
                )

                await message.channel.send(embed=embed)


# COG SETUP
async def setup(bot):
    await bot.add_cog(ViewerCog(bot))
