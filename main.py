import tomllib
import os
import signal
import asyncio
import types
import argparse

from src.srs_app import SrsApp
from src.discord_bot import Bot
from src.dataclasses import BotConfig, SrsConfig, Colors

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action = "store_true", help = "Enable debug mode")

    args = parser.parse_args()

    with open("config.toml", "rb") as f:
        config = tomllib.load(f)

    config_srs = SrsConfig(
        srs_interval = config["srs_interval"],
        path_to_srs_db = config["path_to_srs_db"],
        path_to_full_db = config["path_to_full_db"],
        max_reviews_at_once = config["max_reviews_at_once"],
        entries_before_commit = config["entries_before_commit"],
        match_score_threshold = config["match_score_threshold"]
    )

    srs_app = SrsApp(config_srs)
    srs_app.init_db()

    token_env = config["discord"]["token_env"]
    token = os.getenv(token_env)

    if not token:
        print(f"Unable to retrieve Discord token. Did you set {token_env}?")

        return None

    config_bot = BotConfig(
        srs_app = srs_app,
        token = token,
        prefix = config["discord"]["command_prefix"],
        debug = args.debug
    )

    colors = Colors()
    bot = Bot(config_bot, colors)

    # helper to shutdown
    async def shutdown() -> None:
        srs_app.close_db()

        await bot.bot.close()

        return None

    # handle sigint/sigterm
    def signal_handler(signal: int, frame: types.FrameType) -> None:
        print("\nShutting down!")
        asyncio.create_task(shutdown())

        return None

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    bot.start()

if __name__ in {"__main__"}:
    main()
