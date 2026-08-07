import logging

import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("survey-bot.language")

LANG_CHANNEL_NAME = "🌐│choose-language"

CARD_TEXT = (
    "**🌐 Choose your language / Выберите язык**\n\n"
    "🇬🇧 English is used by default for everyone.\n"
    "🇷🇺 Нажми «Русский», если хочешь получать личные сообщения бота "
    "(подтверждения, ошибки) на русском.\n\n"
    "You can change this anytime by pressing a button again."
)

CONFIRM = {
    "en": "✅ Your language is set to **English**.",
    "ru": "✅ Язык установлен: **Русский**.",
}


def is_lang_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID and any(r.id == config.ADMIN_ROLE_ID for r in interaction.user.roles):
            return True
        raise app_commands.CheckFailure("Нужны права администратора или специальная роль.")
    return app_commands.check(predicate)


class LanguageSelectView(discord.ui.View):
    """Static persistent view — same two buttons everywhere, no per-instance state."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="English", emoji="🇬🇧", style=discord.ButtonStyle.primary, custom_id="lang_select:en")
    async def pick_en(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_lang(interaction, "en")

    @discord.ui.button(label="Русский", emoji="🇷🇺", style=discord.ButtonStyle.secondary, custom_id="lang_select:ru")
    async def pick_ru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_lang(interaction, "ru")

    async def _set_lang(self, interaction: discord.Interaction, lang: str):
        await interaction.client.db.set_user_lang(interaction.user.id, lang)
        await interaction.response.send_message(CONFIRM[lang], ephemeral=True)


class LanguageCog(commands.Cog, name="LanguageCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    language_group = app_commands.Group(name="language", description="Настройка канала выбора языка (EN/RU)")

    @language_group.command(name="setup", description="Создать/назначить канал выбора языка с кнопками EN/RU")
    @app_commands.describe(channel="Существующий канал для карточки (необязательно — иначе бот создаст новый)")
    @is_lang_admin()
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        if channel is None:
            channel = discord.utils.get(guild.text_channels, name=LANG_CHANNEL_NAME.replace("│", "-"))
        if channel is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, add_reactions=False,
                    create_public_threads=False, create_private_threads=False,
                ),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            }
            if config.ADMIN_ROLE_ID:
                admin_role = guild.get_role(config.ADMIN_ROLE_ID)
                if admin_role:
                    overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            channel = await guild.create_text_channel(
                name=LANG_CHANNEL_NAME.replace("│", "-"), overwrites=overwrites,
                reason="Настройка канала выбора языка",
            )
        else:
            await channel.set_permissions(
                guild.default_role, view_channel=True, send_messages=False, add_reactions=False,
            )

        existing = await self.bot.db.get_lang_channel(guild.id)
        if existing and existing["channel_id"] == channel.id and existing["message_id"]:
            try:
                old_msg = await channel.fetch_message(existing["message_id"])
                await old_msg.edit(content=CARD_TEXT, view=LanguageSelectView())
                await interaction.followup.send(f"✅ Карточка уже была там, обновил текст в {channel.mention}.", ephemeral=True)
                return
            except discord.HTTPException:
                pass

        msg = await channel.send(content=CARD_TEXT, view=LanguageSelectView())
        await self.bot.db.set_lang_channel(guild.id, channel.id, msg.id)
        await interaction.followup.send(f"✅ Канал выбора языка готов: {channel.mention}", ephemeral=True)

    @language_group.command(name="stats", description="Сколько людей выбрали EN / RU")
    @is_lang_admin()
    async def stats(self, interaction: discord.Interaction):
        counts = await self.bot.db.count_users_by_lang()
        en, ru = counts.get("en", 0), counts.get("ru", 0)
        await interaction.response.send_message(
            f"🇬🇧 English: **{en}**\n🇷🇺 Русский: **{ru}**\n"
            f"_(Все остальные считаются английским по умолчанию, пока не жали кнопку.)_",
            ephemeral=True,
        )

    @setup.error
    @stats.error
    async def on_admin_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(str(error), ephemeral=True)
        else:
            log.exception("Command error", exc_info=error)
            msg = "Произошла непредвиденная ошибка."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LanguageCog(bot))
