import configparser
import os
import sys
import discord
from discord.ext import commands

# Initialize ConfigParser and load credentials
config = configparser.ConfigParser()
config.read("config.ini")
TOKEN = config["DISCORD"]["TOKEN"]

# Gateway privileged permissions configurations
intents = discord.Intents.default()
intents.guilds = True            # Allows tracking server structure paths
intents.message_content = True   # Allows bot to read input keywords
intents.dm_messages = True       # Allows bot to see incoming direct messages

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print("=" * 50)
    print(f"🤖 CONNECTED SYSTEM: Logged in safely as: {bot.user}")
    print(f"📡 STATE ENGINE: Listening live for direct message threads...")
    print("=" * 50)

@bot.event
async def setup_hook():
    try:
        # Load the central module extension file from cogs/viewer.py
        await bot.load_extension("cogs.viewer")
        print("📥 MEMORY ENGINE: 'cogs.viewer' component extension mounted successfully.")
    except Exception as e:
        print(f"❌ EXTENSION FAULT: Could not inject viewer files into system stream -> {e}")
        sys.exit(1)

bot.run(TOKEN)
