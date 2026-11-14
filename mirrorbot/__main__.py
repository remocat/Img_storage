"""Entry point: python -m mirrorbot [-c config.ini]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import discord

from .bot import MirrorBot
from .config import ConfigError, load_config


class _RedactFilter(logging.Filter):
    """Defense in depth: scrub the token from any record before it is emitted."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secret:
            text = record.getMessage()
            if self._secret in text:
                record.msg = text.replace(self._secret, "[REDACTED]")
                record.args = ()
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mirrorbot", description="Discord image mirror bot")
    parser.add_argument("-c", "--config", default="config.ini", help="path to config.ini")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    handler.addFilter(_RedactFilter(cfg.token))
    logging.basicConfig(level=cfg.log_level, handlers=[handler])

    bot = MirrorBot(cfg)
    try:
        bot.run(cfg.token, log_handler=None)  # we configured logging ourselves
    except discord.LoginFailure:
        print("Login failed: the bot token is invalid or was reset.", file=sys.stderr)
        return 3
    except discord.PrivilegedIntentsRequired:
        print(
            "Message Content Intent is not enabled. Developer Portal -> your app -> Bot -> "
            "Privileged Gateway Intents -> enable MESSAGE CONTENT INTENT.",
            file=sys.stderr,
        )
        return 4
    return bot.exit_code


if __name__ == "__main__":
    sys.exit(main())
