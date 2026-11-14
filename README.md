# Discord Image Mirror

Channels named `<name>-dump` in a **source** guild become commands. `!<name>` browses
that channel's images with ◀ ▶ buttons; `!<name> set` adds an image (with a confirm
step) to the matching channel in a **target** guild. Source and target may be the same guild.

## How it works (design decisions)

| Decision | Choice | Why |
|---|---|---|
| Command parsing | `on_message` prefix parser | Commands come from channel names at runtime. Slash commands would need a re-sync (rate-limited, slow to propagate) on every channel change. The prefix is configurable. |
| Discovery | Channel **name** ends with `suffix` | Free (no history reads), deterministic, no content scanning. |
| Backing store | Discord channels hold the images; SQLite holds only IDs | No re-hosting. Signed CDN URLs expire, so URLs are never stored: they are re-resolved per view and cached briefly. |
| `set` destination | Same-named pool channel in the target guild | Moderators can see and delete uploads; no per-user channel sprawl; no extra permissions. |
| Pool freshness | Bounded backfill at startup + live `on_message` / delete events | No unbounded history scans per request. |

## Commands

| Command | Effect |
|---|---|
| `!help` | Lists every discovered command, its channel, and pool size |
| `!<name>` / `!<name> get` | Shows one image (random or newest, see `get_strategy`) with ◀ older / newer ▶ buttons |
| `!<name> set` | Asks for an image, previews it, uploads only after **Confirm** |
| `!cancel` | Aborts a pending `set` |

`set` sessions expire after `session_timeout_seconds` (default 90): the bot edits its
prompt to say so and discards the session. The confirm preview expires after
`confirm_timeout_seconds` (default 60); nothing is uploaded.

## 1. Create the bot (Discord Developer Portal)

1. https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Reset Token** → copy it (shown once). Put it in `config.ini` or the `DISCORD_TOKEN` env var.
3. Same tab → **Privileged Gateway Intents** → turn on **Message Content Intent** → Save.
4. Optional but recommended for a private bot: untick **Public Bot**.

## 2. Gateway intents

| Intent | Type | Why |
|---|---|---|
| Guilds | standard | channel discovery and channel events |
| Guild Messages / Direct Messages | standard | receive commands and uploads |
| **Message Content** | **privileged (enable in portal)** | read `!commands` in guild channels (DMs are exempt) |

Server Members and Presence are **not** needed. Verification is required for the Message
Content intent only once the bot is in 100+ servers.

## 3. OAuth2 scopes and permissions

- Scope: **`bot`** only (no `applications.commands`; there are no slash commands).
- Bot permissions: **View Channels, Send Messages, Embed Links, Attach Files, Read Message History** (integer `117760`).

Pool channels must not override these away from the bot. Do **not** grant Administrator or Mention Everyone.

## 4. Invite the bot to both guilds

Open this URL once per guild (you need Manage Server there), replacing `APP_ID` with your
Application ID (General Information tab):

```
https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot&permissions=117760
```

If `source_guild_id` equals `target_guild_id`, invite once. If they differ, the bot **must**
be in both or it exits at startup with a clear error. To get a guild ID: Discord → Settings →
Advanced → Developer Mode on, then right-click the server → **Copy Server ID**.

For `set` to work, the target guild needs a channel whose name matches the same pattern
(e.g. `bae-dump`). The bot never creates channels.

Users must share a server with the bot to DM it.

## 5. Run locally

Requires Python 3.11+. From the project root (the folder containing `mirrorbot/`):

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.ini.example config.ini # then edit: token, guild IDs
python -m mirrorbot              # or: python -m mirrorbot -c path/to/config.ini
```

Windows `cp` equivalent: `copy config.ini.example config.ini`.

## Running on a small hosted container (e.g. 256 MB RAM / 25% CPU / 512 MB disk)

- **Start command:** `python main.py` (shim included) or `python -m mirrorbot`. Python 3.11+ is required;
  check the host's Python version setting.
- **Token:** put it in the host's *Environment* tab as `DISCORD_TOKEN` instead of `config.ini`.
- **Memory:** the bot subscribes only to the gateway events it uses, disables the message and member
  caches, and buffers at most 2 images in RAM at a time (each upload is capped at `max_upload_mb`, default 8).
  Expect roughly the baseline of any discord.py bot plus a few MB; this is an estimate, not a measurement.
- **CPU:** nothing runs in a loop. History is read once at startup (bounded by `backfill_limit`) and then
  updated from live events.
- **Disk:** images are never stored locally. `data/index.db` holds IDs only (a few KB per thousand images).
- **Ephemeral disk is fine:** if the host wipes `data/` on restart, the index is rebuilt from the last
  `backfill_limit` messages per channel at startup. Lower `backfill_limit` if startup is slow.

## Configuration

See comments in `config.ini.example`. Highlights:

- `token`: no quotes. `DISCORD_TOKEN` env var overrides it.
- `enforce_membership = true`: users must be members of the relevant guild and be allowed to
  view (get) or post in (set) the channel. Set to `false` only if you accept that anyone who can DM
  the bot can read and write the pools.

## Privacy and logging

- The token is never logged (and a log filter redacts it if it ever appears).
- Logs contain IDs, counts and HTTP status codes only: no image URLs, filenames or message content.
- The SQLite file stores snowflake IDs only.

## Known limitations

- Only **attachments** are indexed (PNG/JPG/GIF/WebP, including forwarded messages). Image links
  pasted as text and embed-only images are not.
- If a message has several images, `set` takes the **first** supported one.
- Pools hold at most the newest `backfill_limit` messages found at startup plus everything seen
  live afterwards. Messages deleted while the bot was offline are pruned lazily when they fail to load.
- When source ≠ target, uploads land in the target guild and are **not** visible to `get`
  (which reads the source). When they are the same guild, uploads join the pool immediately.
- Not load-tested; designed for a handful of guilds, single process, no sharding.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Message Content Intent is not enabled" | Portal → Bot → enable it (step 1.3) |
| "Bot is not a member of guild(s) …" | Invite it (step 4) and check both IDs |
| `!help` shows no commands | No text channel ends with the suffix, or the bot can't see them |
| `get` says the pool is empty | Bot lacks Read Message History there, or no supported attachments in the last `backfill_limit` messages |
| Silent in a server | Bot lacks Send Messages in that channel, or Message Content intent is off |
