import logging

import discord
from discord.ext import commands

import config
from database import Database

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("survey-bot")

intents = discord.Intents.default()


class SurveyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.db = Database(config.DB_PATH)

    async def setup_hook(self):
        await self.db.init()
        await self.load_extension("cogs.survey")

        # Re-register persistent button views so they keep working after a restart.
        from cogs.survey import SurveyStartView
        surveys = await self.db.list_surveys()
        for s in surveys:
            self.add_view(SurveyStartView(s["name"]))

        synced = await self.tree.sync()
        log.info(f"Synced {len(synced)} slash commands, restored {len(surveys)} survey views")

        # Веб-панель поднимается в том же процессе/event loop, чтобы делить
        # с ботом единственный порт, доступный на бесплатных тарифах хостинга.
        from web.panel import start_web_panel
        self.loop.create_task(start_web_panel(self))

    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")


bot = SurveyBot()

if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN не задан. Положи его в переменную окружения или в файл .env "
            "(смотри .env.example)."
        )
    bot.run(config.TOKEN)
