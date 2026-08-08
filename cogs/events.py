import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("survey-bot.events")

STATUS_COLORS = {
    "open": discord.Color.blurple(),
    "full": discord.Color.orange(),
    "closed": discord.Color.dark_grey(),
    "finished": discord.Color.dark_grey(),
}

# Тексты на карточке (embed, подписи кнопок) видят ВСЕ участники сразу —
# Discord не умеет показывать разный текст разным людям на одном сообщении,
# поэтому здесь всегда английский по умолчанию.
# А вот эфемерные ответы на нажатия кнопок видит только сам нажавший —
# их можно показывать на языке, который человек выбрал в /language setup.
PLAYER_MSGS = {
    "en": {
        "event_gone": "This event no longer exists.",
        "already_joined": "You're already signed up for this event ✅",
        "already_waiting": "You're already on the waitlist for this event ⏳",
        "joined": "✅ You're signed up for **{title}**. Access to {channel} is now open.",
        "waitlisted": "⏳ You've been added to the waitlist (position {pos}). "
                       "The organizer reviews waitlist requests manually and will approve you if a spot opens up.",
        "not_signed_up": "You're not signed up for this event.",
        "left_joined": "🚪 Signup cancelled — your role and channel access were removed.",
        "left_waiting": "🚪 You left the waitlist.",
        "welcome": "👋 {mention} just joined the event!",
        "private_channel_fallback": "the private channel",
    },
    "ru": {
        "event_gone": "Это событие больше не существует.",
        "already_joined": "Ты уже записан(а) на это событие ✅",
        "already_waiting": "Ты уже в листе ожидания на это событие ⏳",
        "joined": "✅ Записал(а) тебя на «{title}». Доступ к {channel} открыт.",
        "waitlisted": "⏳ Ты добавлен(а) в лист ожидания (позиция {pos}). "
                       "Организатор разбирает заявки вручную и одобрит тебя, если появится место.",
        "not_signed_up": "Ты не записан(а) на это событие.",
        "left_joined": "🚪 Запись отменена, доступ к приватному каналу и роль сняты.",
        "left_waiting": "🚪 Ты вышел(а) из листа ожидания.",
        "welcome": "👋 {mention} присоединился(ась) к событию!",
        "private_channel_fallback": "приватном канале",
    },
}


def pm(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in PLAYER_MSGS else "en"
    return PLAYER_MSGS[lang][key].format(**kwargs)


def is_event_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID and any(r.id == config.ADMIN_ROLE_ID for r in interaction.user.roles):
            return True
        raise app_commands.CheckFailure(
            "Нужны права администратора или специальная роль, чтобы управлять событиями."
        )
    return app_commands.check(predicate)


def parse_event_dt(date_str: str, time_str: str) -> datetime.datetime:
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
    try:
        return datetime.datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    except ValueError:
        raise ValueError(
            "Неверный формат даты/времени. Дата — ДД.ММ.ГГГГ (например 25.12.2026), "
            "время — ЧЧ:ММ (например 19:30)."
        )


def format_event_dt(iso_str: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return iso_str or "—"


def build_event_embed(event: dict, joined: int, waiting: int) -> discord.Embed:
    capacity = event["capacity"]
    status = event.get("status", "active")
    if status == "finished":
        state = "finished"
        state_label = "✅ Event finished — kept as history"
    elif status == "closed":
        # Ручной режим: организатор не хочет, чтобы люди попадали сразу,
        # даже если формально есть свободные места — все заявки идут в очередь.
        state = "closed"
        state_label = "🔒 Manual approval (applications go to waitlist)"
    elif joined >= capacity:
        state = "full"
        state_label = "🟠 Full — new signups go to waitlist"
    else:
        state = "open"
        state_label = "🟢 Open — instant signup"

    embed = discord.Embed(
        title=f"🎫 {event['title']}",
        description=event.get("description") or "",
        color=STATUS_COLORS[state],
    )
    embed.add_field(name="🗓️ Date & Time", value=format_event_dt(event["event_dt"]), inline=False)
    embed.add_field(name="👥 Participants", value=f"{joined}/{capacity}", inline=True)
    embed.add_field(name="📌 Status", value=state_label, inline=True)
    if waiting:
        embed.add_field(name="⏳ Waitlist", value=str(waiting), inline=True)
    if event.get("role_id"):
        embed.add_field(name="🔑 Role", value=f"<@&{event['role_id']}>", inline=False)
    embed.set_footer(text=f"Event #{event['id']}")
    return embed


class EventCardView(discord.ui.View):
    """Persistent view attached to the event announcement card."""

    def __init__(self, event_id: int):
        super().__init__(timeout=None)
        self.event_id = event_id

        join_btn = discord.ui.Button(
            label="Join",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"event_join:{event_id}",
        )
        join_btn.callback = self.on_join
        self.add_item(join_btn)

        leave_btn = discord.ui.Button(
            label="Leave",
            emoji="🚪",
            style=discord.ButtonStyle.secondary,
            custom_id=f"event_leave:{event_id}",
        )
        leave_btn.callback = self.on_leave
        self.add_item(leave_btn)

    async def on_join(self, interaction: discord.Interaction):
        cog: "EventsCog" = interaction.client.get_cog("EventsCog")
        await cog.handle_join(interaction, self.event_id)

    async def on_leave(self, interaction: discord.Interaction):
        cog: "EventsCog" = interaction.client.get_cog("EventsCog")
        await cog.handle_leave(interaction, self.event_id)


class EventsCog(commands.Cog, name="EventsCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    event_group = app_commands.Group(name="event", description="Управление событиями с ограниченным набором участников")

    @event_group.command(name="create", description="Создать событие и опубликовать карточку в этом канале")
    @app_commands.describe(
        title="Название события",
        description="Описание события",
        capacity="Сколько участников нужно набрать",
        date="Дата в формате ДД.ММ.ГГГГ",
        time="Время в формате ЧЧ:ММ",
        role="Роль, которая будет автоматически выдаваться записавшимся",
    )
    @is_event_admin()
    async def create(self, interaction: discord.Interaction, title: str, description: str,
                      capacity: app_commands.Range[int, 1, 900], date: str, time: str,
                      role: discord.Role):
        try:
            dt = parse_event_dt(date, time)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        event_id, private_channel = await self.create_event_and_publish(
            guild=interaction.guild, announce_channel=interaction.channel, title=title,
            description=description, capacity=capacity, dt=dt, role=role,
            creator=interaction.user,
        )

        await interaction.followup.send(
            f"✅ Событие `#{event_id}` создано и опубликовано в {interaction.channel.mention}.\n"
            f"Приватный канал: {private_channel.mention}\n"
            f"Редактировать/донабрать/управлять листом ожидания можно через веб-панель "
            f"(`/events/{event_id}`).",
            ephemeral=True,
        )

    async def create_event_and_publish(self, *, guild: discord.Guild, announce_channel: discord.TextChannel,
                                        title: str, description: str, capacity: int,
                                        dt: datetime.datetime, role: discord.Role,
                                        creator: discord.Member | None = None,
                                        creator_id: int | None = None):
        """Общая логика создания события: БД-запись + приватный канал + карточка.

        Используется и слэш-командой `/event create`, и веб-панелью — так поведение
        (категория, права доступа, текст карточки) гарантированно не расходится.
        """
        event_id = await self.bot.db.create_event(
            guild_id=guild.id, title=title, description=description, capacity=capacity,
            event_dt=dt.isoformat(), role_id=role.id,
            creator_id=(creator.id if creator else creator_id),
        )

        category = discord.utils.get(guild.categories, name=config.EVENT_CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(config.EVENT_CATEGORY_NAME)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if creator is not None:
            overwrites[creator] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        if config.ADMIN_ROLE_ID:
            admin_role = guild.get_role(config.ADMIN_ROLE_ID)
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)

        safe_name = "".join(c for c in title.lower() if c.isalnum() or c == " ").strip().replace(" ", "-")
        channel_name = f"event-{safe_name}"[:90] or f"event-{event_id}"
        private_channel = await guild.create_text_channel(
            name=channel_name, category=category, overwrites=overwrites,
        )

        embed = build_event_embed(await self.bot.db.get_event(event_id), joined=0, waiting=0)
        view = EventCardView(event_id)
        card_msg = await announce_channel.send(embed=embed, view=view)
        self.bot.add_view(view)

        await self.bot.db.set_event_message(event_id, private_channel.id, announce_channel.id, card_msg.id)

        await private_channel.send(
            f"🎫 Private channel for **{title}**.\n"
            f"Everyone who signs up will show up here."
        )
        return event_id, private_channel

    @event_group.command(name="list", description="Список событий на сервере")
    @is_event_admin()
    async def list_events(self, interaction: discord.Interaction):
        events = await self.bot.db.list_events(guild_id=interaction.guild.id)
        if not events:
            await interaction.response.send_message("Событий пока нет.", ephemeral=True)
            return
        lines = []
        for e in events:
            joined = await self.bot.db.count_participants(e["id"], "joined")
            lines.append(
                f"• `#{e['id']}` **{e['title']}** — {joined}/{e['capacity']}, "
                f"{format_event_dt(e['event_dt'])} [{e['status']}]"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @event_group.command(name="close", description="Перевести набор в ручной режим — новые заявки идут в лист ожидания")
    @is_event_admin()
    async def close_event(self, interaction: discord.Interaction, event_id: int):
        event = await self.bot.db.get_event(event_id)
        if not event or event["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("Событие не найдено.", ephemeral=True)
            return
        await self.bot.db.set_event_status(event_id, "closed")
        await self.refresh_card(event_id)
        await interaction.response.send_message(
            f"🔒 Событие `#{event_id}` переведено в ручной режим: новые заявки будут падать "
            f"в лист ожидания, даже если формально есть места. Одобряй вручную через веб-панель.",
            ephemeral=True,
        )

    @event_group.command(name="delete", description="Удалить событие вместе с приватным каналом")
    @is_event_admin()
    async def delete_event(self, interaction: discord.Interaction, event_id: int):
        event = await self.bot.db.get_event(event_id)
        if not event or event["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("Событие не найдено.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._teardown_event(event, interaction.guild)
        await interaction.followup.send(f"🗑️ Событие `#{event_id}` удалено.", ephemeral=True)

    @create.error
    @list_events.error
    @close_event.error
    @delete_event.error
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

    # ------------------------------------------------------------------
    # Кнопки на карточке
    # ------------------------------------------------------------------

    async def handle_join(self, interaction: discord.Interaction, event_id: int):
        lang = await self.bot.db.get_user_lang(interaction.user.id)
        event = await self.bot.db.get_event(event_id)
        if not event:
            await interaction.response.send_message(pm(lang, "event_gone"), ephemeral=True)
            return

        existing = await self.bot.db.get_participant(event_id, interaction.user.id)
        if existing and existing["status"] == "joined":
            await interaction.response.send_message(pm(lang, "already_joined"), ephemeral=True)
            return
        if existing and existing["status"] == "waiting":
            await interaction.response.send_message(pm(lang, "already_waiting"), ephemeral=True)
            return

        joined_count = await self.bot.db.count_participants(event_id, "joined")
        # Мгновенная запись только если набор в обычном режиме (не "закрыт" вручную)
        # И есть свободные места. Иначе — в лист ожидания, организатор одобряет сам.
        instant = (event["status"] == "active") and (joined_count < event["capacity"])
        if instant:
            await self.bot.db.add_participant(event_id, interaction.user.id, "joined")
            await self._grant_access(interaction.guild, event, interaction.user)
            await self.refresh_card(event_id)
            channel = interaction.guild.get_channel(event["channel_id"])
            mention = channel.mention if channel else pm(lang, "private_channel_fallback")
            await interaction.response.send_message(
                pm(lang, "joined", title=event["title"], channel=mention), ephemeral=True,
            )
        else:
            await self.bot.db.add_participant(event_id, interaction.user.id, "waiting")
            waiting_pos = await self.bot.db.count_participants(event_id, "waiting")
            await self.refresh_card(event_id)
            await interaction.response.send_message(
                pm(lang, "waitlisted", pos=waiting_pos), ephemeral=True,
            )

    async def handle_leave(self, interaction: discord.Interaction, event_id: int):
        lang = await self.bot.db.get_user_lang(interaction.user.id)
        event = await self.bot.db.get_event(event_id)
        if not event:
            await interaction.response.send_message(pm(lang, "event_gone"), ephemeral=True)
            return

        existing = await self.bot.db.get_participant(event_id, interaction.user.id)
        if not existing:
            await interaction.response.send_message(pm(lang, "not_signed_up"), ephemeral=True)
            return

        was_joined = existing["status"] == "joined"
        await self.bot.db.remove_participant(event_id, interaction.user.id)
        if was_joined:
            await self._revoke_access(interaction.guild, event, interaction.user)
            msg = pm(lang, "left_joined")
        else:
            msg = pm(lang, "left_waiting")
        await self.refresh_card(event_id)
        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # Вспомогательное (используется и веб-панелью)
    # ------------------------------------------------------------------

    async def _resolve_member(self, guild: discord.Guild, user_id: int):
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
        return member

    async def _grant_access(self, guild: discord.Guild, event: dict, member: discord.Member):
        if event.get("role_id"):
            role = guild.get_role(event["role_id"])
            if role:
                try:
                    await member.add_roles(role, reason=f"Запись на событие #{event['id']}")
                except discord.HTTPException:
                    log.warning("Не удалось выдать роль %s участнику %s", role.id, member.id)
        channel = guild.get_channel(event["channel_id"]) if event.get("channel_id") else None
        if channel:
            try:
                await channel.set_permissions(
                    member, view_channel=True, send_messages=True, read_message_history=True,
                    reason=f"Запись на событие #{event['id']}",
                )
                lang = await self.bot.db.get_user_lang(member.id)
                await channel.send(pm(lang, "welcome", mention=member.mention))
            except discord.HTTPException:
                log.warning("Не удалось дать доступ к каналу %s участнику %s", channel.id, member.id)

    async def _revoke_access(self, guild: discord.Guild, event: dict, member: discord.Member | discord.Object):
        if event.get("role_id"):
            role = guild.get_role(event["role_id"])
            if role:
                m = member if isinstance(member, discord.Member) else await self._resolve_member(guild, member.id)
                if m:
                    try:
                        await m.remove_roles(role, reason=f"Выход из события #{event['id']}")
                    except discord.HTTPException:
                        pass
        channel = guild.get_channel(event["channel_id"]) if event.get("channel_id") else None
        if channel:
            try:
                await channel.set_permissions(member, overwrite=None)
            except discord.HTTPException:
                pass

    async def refresh_card(self, event_id: int):
        event = await self.bot.db.get_event(event_id)
        if not event or not event.get("announce_channel_id") or not event.get("announce_message_id"):
            return
        channel = self.bot.get_channel(event["announce_channel_id"])
        if not channel:
            return
        try:
            message = await channel.fetch_message(event["announce_message_id"])
        except (discord.NotFound, discord.HTTPException):
            return
        joined = await self.bot.db.count_participants(event_id, "joined")
        waiting = await self.bot.db.count_participants(event_id, "waiting")
        embed = build_event_embed(event, joined, waiting)
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass

    async def _finish_event(self, event: dict, guild: discord.Guild):
        """Завершить событие: в отличие от _teardown_event, запись в БД и
        история участников/листа ожидания НЕ удаляются — событие остаётся
        доступным в веб-панели как архив. Удаляется только приватный канал
        (он больше не нужен) и обновляется карточка в Discord — кнопка
        "Join" убирается, статус меняется на "finished". Роль у участников
        не трогаем — она остаётся как память об участии."""
        if event.get("channel_id"):
            channel = guild.get_channel(event["channel_id"])
            if channel:
                try:
                    await channel.delete(reason=f"Событие #{event['id']} завершено")
                except discord.HTTPException:
                    pass
        await self.bot.db.set_event_status(event["id"], "finished")
        if event.get("announce_channel_id") and event.get("announce_message_id"):
            ann_channel = guild.get_channel(event["announce_channel_id"])
            if ann_channel:
                try:
                    msg = await ann_channel.fetch_message(event["announce_message_id"])
                    fresh_event = await self.bot.db.get_event(event["id"])
                    joined = await self.bot.db.count_participants(event["id"], "joined")
                    embed = build_event_embed(fresh_event, joined, 0)
                    await msg.edit(content="✅ *Это событие завершено.*", embed=embed, view=None)
                except discord.HTTPException:
                    pass

    async def _teardown_event(self, event: dict, guild: discord.Guild):
        # Роль у участников больше НЕ снимаем — она остаётся как память/бейдж
        # о том, что человек был на этом событии. Снимаем только приватный
        # канал и помечаем карточку. Роль по-прежнему снимается, если человек
        # сам выходит из записи до завершения события (см. leave-flow выше).
        if event.get("channel_id"):
            channel = guild.get_channel(event["channel_id"])
            if channel:
                try:
                    await channel.delete(reason="Событие удалено")
                except discord.HTTPException:
                    pass
        if event.get("announce_channel_id") and event.get("announce_message_id"):
            ann_channel = guild.get_channel(event["announce_channel_id"])
            if ann_channel:
                try:
                    msg = await ann_channel.fetch_message(event["announce_message_id"])
                    await msg.edit(content="🗑️ *Это событие было удалено.*", embed=None, view=None)
                except discord.HTTPException:
                    pass
        await self.bot.db.delete_event(event["id"])


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
