import discord

class Gallery:

    def __init__(self):
        # Using a dictionary layout matching explicit lookup requirements
        self.posts = {"images": []}

    async def load_gallery(self, channel):
        self.posts["images"].clear()
        
        all_images = []
        valid_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp")

        print(f"   ↳ [SCANNING] Reading historical logs inside channel: #{channel.name}")

        async for message in channel.history(limit=100):
            
            # --- SOURCE A: STANDARD DIRECT FILE ATTACHMENTS ---
            for attachment in message.attachments:
                if attachment.filename.lower().endswith(valid_extensions):
                    all_images.append(attachment.url)
            
            # --- SOURCE B: MOUNTED EMBED IMAGES ---
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    all_images.append(embed.image.url)
                elif embed.url and any(ext in embed.url.lower() for ext in valid_extensions):
                    all_images.append(embed.url)
            
            # --- SOURCE C: DISCORD FORWARDED MESSAGE SNAPSHOT LAYOUTS ---
            # Unpacks image collections that were forwarded as grids from other channels
            if hasattr(message, "message_snapshots") and message.message_snapshots:
                for snapshot in message.message_snapshots:
                    
                    if hasattr(snapshot, "attachments"):
                        for snap_attach in snapshot.attachments:
                            if snap_attach.filename.lower().endswith(valid_extensions):
                                all_images.append(snap_attach.url)
                    
                    if hasattr(snapshot, "embeds"):
                        for snap_embed in snapshot.embeds:
                            if snap_embed.image and snap_embed.image.url:
                                all_images.append(snap_embed.image.url)
                            elif snap_embed.url and any(ext in snap_embed.url.lower() for ext in valid_extensions):
                                all_images.append(snap_embed.url)

            # --- SOURCE D: PLAIN PASTED CHAT TEXT LINKS ---
            words = message.content.split()
            for word in words:
                if word.startswith(("http://", "https://")) and any(ext in word.lower() for ext in valid_extensions):
                    all_images.append(word)

        # Deduplicate files safely
        all_images = list(dict.fromkeys(all_images))
        all_images.reverse()

        if all_images:
            self.posts["images"] = all_images
            print(f"     ↳ [SUCCESS] Cached {len(all_images)} verified image elements for #{channel.name}")
        else:
            print(f"     ⚠️ [EMPTY] Found 0 image files inside #{channel.name}.")
