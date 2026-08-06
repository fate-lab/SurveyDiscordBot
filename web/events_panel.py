"""
Веб-страницы для управления событиями (карточки с ограниченным набором
участников): создание, редактирование, донабор и разбор листа ожидания —
всё делается прямо здесь, включая выбор роли и канала публикации.
"""
import csv
import html
import io
import logging

import discord
from aiohttp import web

from cogs.events import parse_event_dt, format_event_dt

log = logging.getLogger("survey-bot.web.events")


async def event_new_get(request: web.Request):
    from web.panel import page
    bot = request.app["bot"]
    guilds = list(bot.guilds)
    if not guilds:
        body = "<div class='card'><p class='muted'>Бот пока не подключён ни к одному серверу.</p></div>"
        return web.Response(text=page("Новое событие", body), content_type="text/html")

    guild = None
    guild_id_param = request.query.get("guild")
    if guild_id_param:
        guild = bot.get_guild(int(guild_id_param))
    elif len(guilds) == 1:
        guild = guilds[0]

    error = ""
    if request.query.get("error"):
        error = f"<p class='error'>{html.escape(request.query['error'])}</p>"

    if guild is None:
        options = "".join(f"<option value='{g.id}'>{html.escape(g.name)}</option>" for g in guilds)
        body = f"""
        <div class="card">
          <h2 style="margin-top:0">На каком сервере создаём событие?</h2>
          {error}
          <form method="get" action="/events/new">
            <label>Сервер</label>
            <select name="guild">{options}</select>
            <input type="submit" value="Далее">
          </form>
        </div>
        """
        return web.Response(text=page("Новое событие", body), content_type="text/html")

    roles = [r for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
             if not r.is_default() and not r.managed]
    channels = sorted(guild.text_channels, key=lambda c: (c.category.position if c.category else -1, c.position))

    if not roles:
        error += "<p class='error'>На сервере нет ролей, которые можно выдать (кроме служебных/@everyone). Создай роль в Discord и обнови страницу.</p>"
    if not channels:
        error += "<p class='error'>На сервере нет текстовых каналов, куда можно опубликовать карточку.</p>"

    role_options = "".join(f"<option value='{r.id}'>{html.escape(r.name)}</option>" for r in roles)
    channel_options = "".join(
        f"<option value='{c.id}'>#{html.escape(c.name)}"
        f"{(' (' + c.category.name + ')') if c.category else ''}</option>"
        for c in channels
    )
    switch_server = ""
    if len(guilds) > 1:
        switch_server = f"<p class='muted'><a href='/events/new'>Сменить сервер</a></p>"

    body = f"""
    <div class="card">
      <h2 style="margin-top:0">Новое событие — {html.escape(guild.name)}</h2>
      {switch_server}
      {error}
      <form method="post" action="/events/new">
        <input type="hidden" name="guild_id" value="{guild.id}">
        <label>Название</label>
        <input type="text" name="title" required>
        <label>Описание</label>
        <textarea name="description" rows="3"></textarea>
        <div class="row">
          <div>
            <label>Нужно участников</label>
            <input type="number" name="capacity" min="1" value="10" required>
          </div>
          <div>
            <label>Дата (ДД.ММ.ГГГГ)</label>
            <input type="text" name="date" placeholder="25.12.2026" required>
          </div>
          <div>
            <label>Время (ЧЧ:ММ)</label>
            <input type="text" name="time" placeholder="19:30" required>
          </div>
        </div>
        <label>Канал для публикации карточки</label>
        <select name="channel_id" required>{channel_options}</select>
        <label>Роль, которая будет автоматически выдаваться участникам</label>
        <select name="role_id" required>{role_options}</select>
        <input type="submit" value="Создать событие">
      </form>
    </div>
    """
    return web.Response(text=page("Новое событие", body), content_type="text/html")


async def event_new_post(request: web.Request):
    bot = request.app["bot"]
    data = await request.post()

    try:
        guild_id = int(data.get("guild_id") or 0)
    except ValueError:
        guild_id = 0
    guild = bot.get_guild(guild_id)
    if not guild:
        raise web.HTTPFound("/events/new?error=Сервер+не+найден+—+возможно+бот+вышел+с+него")

    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    try:
        capacity = int(data.get("capacity") or 0)
    except ValueError:
        capacity = 0

    if not title:
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error=Название+не+может+быть+пустым")
    if capacity < 1:
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error=Число+участников+должно+быть+больше+0")

    try:
        dt = parse_event_dt(data.get("date", ""), data.get("time", ""))
    except ValueError as e:
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error={html.escape(str(e))}")

    try:
        channel_id = int(data.get("channel_id") or 0)
        role_id = int(data.get("role_id") or 0)
    except ValueError:
        channel_id = role_id = 0
    channel = guild.get_channel(channel_id)
    role = guild.get_role(role_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error=Выбери+канал+для+публикации")
    if not role:
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error=Выбери+роль+участника")

    cog = _events_cog(bot)
    if not cog:
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error=Модуль+событий+не+загружен+в+боте")

    try:
        event_id, _private_channel = await cog.create_event_and_publish(
            guild=guild, announce_channel=channel, title=title, description=description,
            capacity=capacity, dt=dt, role=role, creator=None,
        )
    except discord.Forbidden:
        raise web.HTTPFound(
            f"/events/new?guild={guild_id}&error=У+бота+не+хватает+прав+—+нужны+права+"
            f"«Управление+каналами»+и+«Управление+ролями»+(роль+бота+должна+быть+выше+выдаваемой+роли)"
        )
    except discord.HTTPException as e:
        raise web.HTTPFound(f"/events/new?guild={guild_id}&error=Ошибка+Discord:+{html.escape(str(e))}")

    raise web.HTTPFound(f"/events/{event_id}?ok=Событие+создано+и+опубликовано")


def _events_cog(bot):
    return bot.get_cog("EventsCog")


async def _resolve_member(guild: discord.Guild, user_id: int):
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
    return member


def _display_name(bot, user_id: int) -> str:
    user = bot.get_user(user_id)
    return html.escape(f"{user}" if user else f"ID {user_id}")


STATUS_LABELS = {
    "active": "🟢 активно",
    "closed": "🔒 закрыто",
}


async def events_list(request: web.Request):
    from web.panel import page  # локальный импорт — избегаем циклической зависимости
    bot = request.app["bot"]
    events = await bot.db.list_events()
    rows = []
    for e in events:
        joined = await bot.db.count_participants(e["id"], "joined")
        waiting = await bot.db.count_participants(e["id"], "waiting")
        guild = bot.get_guild(e["guild_id"])
        rows.append(
            f"<tr><td><a href='/events/{e['id']}'>#{e['id']} {html.escape(e['title'])}</a></td>"
            f"<td>{html.escape(guild.name if guild else str(e['guild_id']))}</td>"
            f"<td>{joined}/{e['capacity']}"
            + (f" <span class='pill'>+{waiting} в очереди</span>" if waiting else "")
            + f"</td>"
            f"<td>{html.escape(format_event_dt(e['event_dt']))}</td>"
            f"<td>{STATUS_LABELS.get(e['status'], e['status'])}</td></tr>"
        )
    rows_html = "".join(rows) or "<tr><td colspan='5' class='muted'>Событий пока нет.</td></tr>"
    body = f"""
    <div class="card">
      <div class="row" style="align-items:center;justify-content:space-between;">
        <h2 style="margin:0">События</h2>
        <a class="btn" href="/events/new">+ Новое событие</a>
      </div>
      <p class="muted">Событие создаётся прямо здесь: название, описание, число мест,
      дата/время, канал публикации и роль для автовыдачи выбираются на сайте — бот сам
      опубликует карточку и создаст приватный канал. Ниже — редактирование, донабор
      и разбор листа ожидания уже созданных событий.</p>
    </div>
    <div class="card">
      <table>
        <tr><th>Событие</th><th>Сервер</th><th>Участники</th><th>Дата</th><th>Статус</th></tr>
        {rows_html}
      </table>
    </div>
    """
    return web.Response(text=page("События", body), content_type="text/html")


def _participant_rows(bot, guild, participants, event_id, action_prefix, status):
    rows = []
    for p in participants:
        name = _display_name(bot, p["user_id"])
        if status == "waiting":
            actions = (
                f"<form method='post' action='/events/{event_id}/waitlist/{p['user_id']}/approve' style='display:inline'>"
                f"<button type='submit'>Одобрить</button></form> "
                f"<form method='post' action='/events/{event_id}/waitlist/{p['user_id']}/reject' style='display:inline'>"
                f"<button class='danger' type='submit'>Отклонить</button></form>"
            )
        else:
            actions = (
                f"<form method='post' action='/events/{event_id}/participants/{p['user_id']}/remove' style='display:inline'>"
                f"<button class='danger' type='submit'>Убрать</button></form>"
            )
        rows.append(f"<tr><td>{name}</td><td>{p['joined_at'] or ''}</td><td>{actions}</td></tr>")
    return "".join(rows) or "<tr><td colspan='3' class='muted'>Пусто.</td></tr>"


async def event_detail(request: web.Request):
    from web.panel import page
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPNotFound(text="Событие не найдено")

    guild = bot.get_guild(event["guild_id"])
    joined = await bot.db.list_participants(event_id, "joined")
    waiting = await bot.db.list_participants(event_id, "waiting")
    role = guild.get_role(event["role_id"]) if guild and event.get("role_id") else None
    channel = guild.get_channel(event["channel_id"]) if guild and event.get("channel_id") else None

    error = ""
    if request.query.get("error"):
        error = f"<p class='error'>{html.escape(request.query['error'])}</p>"
    notice = ""
    if request.query.get("ok"):
        notice = f"<p class='muted'>{html.escape(request.query['ok'])}</p>"

    date_str, time_str = "", ""
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(event["event_dt"])
        date_str, time_str = dt.strftime("%d.%m.%Y"), dt.strftime("%H:%M")
    except (ValueError, TypeError):
        pass

    body = f"""
    <div class="card">
      <div class="row" style="align-items:center;justify-content:space-between;">
        <h2 style="margin:0">#{event_id} {html.escape(event['title'])}</h2>
        <span class="pill">{STATUS_LABELS.get(event['status'], event['status'])}</span>
      </div>
      <p class="muted">
        Сервер: {html.escape(guild.name if guild else '—')} ·
        Роль: {html.escape(role.name) if role else '—'} ·
        Приватный канал: {('#' + channel.name) if channel else '—'}
      </p>
      {error}{notice}
    </div>

    <div class="card">
      <h3>Редактировать / донабрать</h3>
      <form method="post" action="/events/{event_id}/edit">
        <label>Название</label>
        <input type="text" name="title" value="{html.escape(event['title'])}" required>
        <label>Описание</label>
        <textarea name="description" rows="4">{html.escape(event['description'] or '')}</textarea>
        <div class="row">
          <div>
            <label>Нужно участников (можно увеличить — это и есть донабор)</label>
            <input type="number" name="capacity" min="{len(joined)}" value="{event['capacity']}" required>
          </div>
        </div>
        <div class="row">
          <div>
            <label>Дата (ДД.ММ.ГГГГ)</label>
            <input type="text" name="date" value="{date_str}" required>
          </div>
          <div>
            <label>Время (ЧЧ:ММ)</label>
            <input type="text" name="time" value="{time_str}" required>
          </div>
        </div>
        <input type="submit" value="Сохранить изменения">
      </form>
      <p class="muted">Роль и приватный канал закрепляются при создании события и здесь не меняются
      (чтобы поменять — проще пересоздать событие).</p>
    </div>

    <div class="card">
      <h3>Участники ({len(joined)}/{event['capacity']})</h3>
      <table>
        <tr><th>Пользователь</th><th>Записался</th><th></th></tr>
        {_participant_rows(bot, guild, joined, event_id, "participants", "joined")}
      </table>
      <p><a class="btn secondary" href="/events/{event_id}/export.csv">Скачать список (CSV)</a></p>
    </div>

    <div class="card">
      <h3>Лист ожидания ({len(waiting)})</h3>
      <table>
        <tr><th>Пользователь</th><th>Записался</th><th></th></tr>
        {_participant_rows(bot, guild, waiting, event_id, "waitlist", "waiting")}
      </table>
      <p class="muted">Одобрить можно, только если в событии ещё есть свободные места — сперва донаберите места выше, если нужно.</p>
    </div>

    <div class="card">
      <h3>Опасная зона</h3>
      <form method="post" action="/events/{event_id}/{'reopen' if event['status'] == 'closed' else 'close'}" style="display:inline">
        <button class="secondary" type="submit">{'Открыть набор снова' if event['status'] == 'closed' else 'Закрыть набор'}</button>
      </form>
      <form method="post" action="/events/{event_id}/delete" style="display:inline"
            onsubmit="return confirm('Удалить событие и приватный канал безвозвратно?')">
        <button class="danger" type="submit">Удалить событие</button>
      </form>
    </div>
    """
    return web.Response(text=page(event["title"], body), content_type="text/html")


async def event_edit_post(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPNotFound(text="Событие не найдено")
    data = await request.post()
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    try:
        capacity = int(data.get("capacity") or 0)
    except ValueError:
        capacity = 0
    joined_count = await bot.db.count_participants(event_id, "joined")

    if not title:
        raise web.HTTPFound(f"/events/{event_id}?error=Название+не+может+быть+пустым")
    if capacity < 1:
        raise web.HTTPFound(f"/events/{event_id}?error=Число+участников+должно+быть+больше+0")
    if capacity < joined_count:
        raise web.HTTPFound(
            f"/events/{event_id}?error=Нельзя+поставить+лимит+меньше+уже+записанных+({joined_count})"
        )
    try:
        dt = parse_event_dt(data.get("date", ""), data.get("time", ""))
    except ValueError as e:
        raise web.HTTPFound(f"/events/{event_id}?error={html.escape(str(e))}")

    await bot.db.update_event(event_id, title, description, capacity, dt.isoformat())
    cog = _events_cog(bot)
    if cog:
        await cog.refresh_card(event_id)
    raise web.HTTPFound(f"/events/{event_id}?ok=Изменения+сохранены")


async def waitlist_approve(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    user_id = int(request.match_info["user_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPNotFound(text="Событие не найдено")
    guild = bot.get_guild(event["guild_id"])
    if not guild:
        raise web.HTTPFound(f"/events/{event_id}?error=Сервер+недоступен+боту")

    joined_count = await bot.db.count_participants(event_id, "joined")
    if joined_count >= event["capacity"]:
        raise web.HTTPFound(f"/events/{event_id}?error=Нет+свободных+мест+—+сначала+увеличьте+лимит")

    participant = await bot.db.get_participant(event_id, user_id)
    if not participant or participant["status"] != "waiting":
        raise web.HTTPFound(f"/events/{event_id}?error=Пользователь+не+в+листе+ожидания")

    member = await _resolve_member(guild, user_id)
    await bot.db.set_participant_status(event_id, user_id, "joined")
    cog = _events_cog(bot)
    if cog and member:
        await cog._grant_access(guild, event, member)
    if cog:
        await cog.refresh_card(event_id)
    raise web.HTTPFound(f"/events/{event_id}?ok=Участник+одобрен")


async def waitlist_reject(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    user_id = int(request.match_info["user_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPNotFound(text="Событие не найдено")
    await bot.db.remove_participant(event_id, user_id)
    cog = _events_cog(bot)
    if cog:
        await cog.refresh_card(event_id)
    raise web.HTTPFound(f"/events/{event_id}?ok=Заявка+отклонена")


async def participant_remove(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    user_id = int(request.match_info["user_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPNotFound(text="Событие не найдено")
    guild = bot.get_guild(event["guild_id"])
    member = await _resolve_member(guild, user_id) if guild else None
    await bot.db.remove_participant(event_id, user_id)
    cog = _events_cog(bot)
    if cog and guild and member:
        await cog._revoke_access(guild, event, member)
    if cog:
        await cog.refresh_card(event_id)
    raise web.HTTPFound(f"/events/{event_id}?ok=Участник+удалён+из+события")


async def event_close(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    await bot.db.set_event_status(event_id, "closed")
    cog = _events_cog(bot)
    if cog:
        await cog.refresh_card(event_id)
    raise web.HTTPFound(f"/events/{event_id}?ok=Набор+закрыт")


async def event_reopen(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    await bot.db.set_event_status(event_id, "active")
    cog = _events_cog(bot)
    if cog:
        await cog.refresh_card(event_id)
    raise web.HTTPFound(f"/events/{event_id}?ok=Набор+снова+открыт")


async def event_delete(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPFound("/events")
    guild = bot.get_guild(event["guild_id"])
    cog = _events_cog(bot)
    if cog and guild:
        await cog._teardown_event(event, guild)
    else:
        await bot.db.delete_event(event_id)
    raise web.HTTPFound("/events")


async def event_export_csv(request: web.Request):
    bot = request.app["bot"]
    event_id = int(request.match_info["event_id"])
    event = await bot.db.get_event(event_id)
    if not event:
        raise web.HTTPNotFound(text="Событие не найдено")
    participants = await bot.db.list_participants(event_id, "joined")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Пользователь", "Discord ID", "Записался (UTC)"])
    for p in participants:
        user = bot.get_user(p["user_id"])
        writer.writerow([str(user) if user else "", p["user_id"], p["joined_at"]])
    buf.seek(0)
    return web.Response(
        body=buf.getvalue().encode("utf-8-sig"),
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="event_{event_id}_participants.csv"'},
    )


def add_routes(app: web.Application):
    app.router.add_get("/events", events_list)
    app.router.add_get("/events/new", event_new_get)
    app.router.add_post("/events/new", event_new_post)
    app.router.add_get("/events/{event_id}", event_detail)
    app.router.add_post("/events/{event_id}/edit", event_edit_post)
    app.router.add_post("/events/{event_id}/waitlist/{user_id}/approve", waitlist_approve)
    app.router.add_post("/events/{event_id}/waitlist/{user_id}/reject", waitlist_reject)
    app.router.add_post("/events/{event_id}/participants/{user_id}/remove", participant_remove)
    app.router.add_post("/events/{event_id}/close", event_close)
    app.router.add_post("/events/{event_id}/reopen", event_reopen)
    app.router.add_post("/events/{event_id}/delete", event_delete)
    app.router.add_get("/events/{event_id}/export.csv", event_export_csv)
