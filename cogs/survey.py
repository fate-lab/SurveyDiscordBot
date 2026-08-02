import asyncio
import io
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from csv_export import build_csv_bytes

log = logging.getLogger("survey-bot")

ALLOWED_TYPES = {"scale", "single", "multi", "yes_no_detail", "text"}


def validate_questions(questions):
    if not isinstance(questions, list) or not questions:
        raise ValueError("Поле 'questions' должно быть непустым списком.")
    for i, q in enumerate(questions):
        if "type" not in q or q["type"] not in ALLOWED_TYPES:
            raise ValueError(
                f"Вопрос {i + 1}: неизвестный или отсутствующий 'type'. "
                f"Допустимые типы: {', '.join(sorted(ALLOWED_TYPES))}"
            )
        if "text" not in q or not str(q["text"]).strip():
            raise ValueError(f"Вопрос {i + 1}: отсутствует 'text'")
        if q["type"] in ("single", "multi") and not q.get("options"):
            raise ValueError(f"Вопрос {i + 1}: для типа '{q['type']}' нужен непустой список 'options'")
        if q["type"] == "scale":
            q.setdefault("min", 1)
            q.setdefault("max", 5)


def is_survey_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID and any(r.id == config.ADMIN_ROLE_ID for r in interaction.user.roles):
            return True
        raise app_commands.CheckFailure(
            "Нужны права администратора или специальная роль, чтобы управлять опросами."
        )
    return app_commands.check(predicate)


class FreeTextModal(discord.ui.Modal):
    def __init__(self, title: str, label: str, long: bool = False):
        super().__init__(title=title[:45])
        self.value = None
        style = discord.TextStyle.paragraph if long else discord.TextStyle.short
        self.input = discord.ui.TextInput(label=label[:45], style=style, required=True, max_length=1000)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        self.value = str(self.input.value)
        await interaction.response.send_message("Ответ принят ✅", ephemeral=True)
        self.stop()


class SurveyStartView(discord.ui.View):
    """Persistent view attached to the published survey message."""

    def __init__(self, survey_name: str):
        super().__init__(timeout=None)
        self.survey_name = survey_name
        button = discord.ui.Button(
            label="📋 Пройти опрос",
            style=discord.ButtonStyle.primary,
            custom_id=f"survey_start:{survey_name}",
        )
        button.callback = self.start_survey
        self.add_item(button)

    async def start_survey(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("SurveyCog")
        if cog is None:
            await interaction.response.send_message("Бот перезагружается, попробуй чуть позже.", ephemeral=True)
            return
        await cog.begin_survey(interaction, self.survey_name)


class SurveyRunner:
    """Walks a single respondent through all questions inside their private channel."""

    def __init__(self, cog, channel: discord.TextChannel, member: discord.Member, survey: dict):
        self.cog = cog
        self.channel = channel
        self.member = member
        self.survey = survey
        self.answers = {}

    async def run(self):
        for idx, q in enumerate(self.survey["questions"]):
            answer = await self.ask(idx, q)
            self.answers[str(idx)] = {"question": q["text"], "answer": answer}
        await self.finish()

    async def ask(self, idx, q):
        qtype = q["type"]
        if qtype == "scale":
            return await self._ask_scale(idx, q)
        if qtype == "single":
            return await self._ask_single(idx, q)
        if qtype == "multi":
            return await self._ask_multi(idx, q)
        if qtype == "yes_no_detail":
            return await self._ask_yes_no_detail(idx, q)
        if qtype == "text":
            return await self._ask_text(idx, q)
        return None

    async def _wait(self, future, view, msg):
        try:
            result = await asyncio.wait_for(future, timeout=600)
        except asyncio.TimeoutError:
            result = "(нет ответа — время вышло)"
        for item in view.children:
            item.disabled = True
        try:
            await msg.edit(view=view)
        except discord.HTTPException:
            pass
        return result

    async def _ask_scale(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        lo, hi = q.get("min", 1), q.get("max", 5)
        view = discord.ui.View(timeout=600)
        for n in range(lo, hi + 1):
            btn = discord.ui.Button(label=str(n), style=discord.ButtonStyle.secondary)

            async def cb(interaction, n=n):
                await interaction.response.defer()
                if not future.done():
                    future.set_result(str(n))

            btn.callback = cb
            view.add_item(btn)
        hint = ""
        if q.get("min_label") or q.get("max_label"):
            hint = f"\n_(​{lo} — {q.get('min_label', '')}, {hi} — {q.get('max_label', '')})_"
        msg = await self.channel.send(f"**{idx + 1}. {q['text']}**{hint}", view=view)
        return await self._wait(future, view, msg)

    async def _ask_single(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        options = list(q["options"])
        has_other = q.get("other_option", False)
        select_options = [discord.SelectOption(label=o[:100], value=o) for o in options]
        if has_other:
            select_options.append(discord.SelectOption(label="Другое", value="__other__"))
        view = discord.ui.View(timeout=600)
        select = discord.ui.Select(placeholder="Выбери вариант...", options=select_options,
                                    min_values=1, max_values=1)

        async def cb(interaction):
            value = select.values[0]
            if value == "__other__":
                modal = FreeTextModal(title="Другое", label="Уточни свой вариант")
                await interaction.response.send_modal(modal)
                await modal.wait()
                if not future.done():
                    future.set_result(f"Другое: {modal.value or ''}")
            else:
                await interaction.response.defer()
                if not future.done():
                    future.set_result(value)

        select.callback = cb
        view.add_item(select)
        msg = await self.channel.send(f"**{idx + 1}. {q['text']}**", view=view)
        return await self._wait(future, view, msg)

    async def _ask_multi(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        options = list(q["options"])
        has_other = q.get("other_option", False)
        select_options = [discord.SelectOption(label=o[:100], value=o) for o in options]
        if has_other:
            select_options.append(discord.SelectOption(label="Другое", value="__other__"))
        view = discord.ui.View(timeout=600)
        select = discord.ui.Select(
            placeholder="Выбери один или несколько вариантов...",
            options=select_options, min_values=1, max_values=len(select_options),
        )
        confirm_btn = discord.ui.Button(label="✅ Готово", style=discord.ButtonStyle.success)
        state = {"values": []}

        async def select_cb(interaction):
            state["values"] = list(select.values)
            await interaction.response.defer()

        async def confirm_cb(interaction):
            values = state["values"]
            if not values:
                await interaction.response.send_message("Выбери хотя бы один вариант перед тем, как нажать «Готово».", ephemeral=True)
                return
            if "__other__" in values:
                modal = FreeTextModal(title="Другое", label="Уточни свой вариант")
                await interaction.response.send_modal(modal)
                await modal.wait()
                values = [v for v in values if v != "__other__"]
                if modal.value:
                    values.append(f"Другое: {modal.value}")
            else:
                await interaction.response.defer()
            if not future.done():
                future.set_result(", ".join(values))

        select.callback = select_cb
        confirm_btn.callback = confirm_cb
        view.add_item(select)
        view.add_item(confirm_btn)
        msg = await self.channel.send(f"**{idx + 1}. {q['text']}** _(можно выбрать несколько, затем нажми «Готово»)_", view=view)
        return await self._wait(future, view, msg)

    async def _ask_yes_no_detail(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        view = discord.ui.View(timeout=600)
        yes_btn = discord.ui.Button(label=q.get("yes_label", "Да"), style=discord.ButtonStyle.success)
        no_btn = discord.ui.Button(label=q.get("no_label", "Нет"), style=discord.ButtonStyle.secondary)

        async def yes_cb(interaction):
            modal = FreeTextModal(title="Опиши коротко", label="Что случилось?", long=True)
            await interaction.response.send_modal(modal)
            await modal.wait()
            if not future.done():
                future.set_result(f"Да: {modal.value or ''}")

        async def no_cb(interaction):
            await interaction.response.defer()
            if not future.done():
                future.set_result("Нет")

        yes_btn.callback = yes_cb
        no_btn.callback = no_cb
        view.add_item(yes_btn)
        view.add_item(no_btn)
        msg = await self.channel.send(f"**{idx + 1}. {q['text']}**", view=view)
        return await self._wait(future, view, msg)

    async def _ask_text(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        view = discord.ui.View(timeout=600)
        btn = discord.ui.Button(label="✍️ Ответить", style=discord.ButtonStyle.primary)

        async def cb(interaction):
            modal = FreeTextModal(title="Твой ответ", label=q["text"][:45], long=True)
            await interaction.response.send_modal(modal)
            await modal.wait()
            if not future.done():
                future.set_result(modal.value or "")

        btn.callback = cb
        view.add_item(btn)
        msg = await self.channel.send(f"**{idx + 1}. {q['text']}**", view=view)
        return await self._wait(future, view, msg)

    async def finish(self):
        survey = await self.cog.bot.db.get_survey(self.survey["name"])
        await self.cog.bot.db.save_response(survey["id"], self.member.id, self.answers)
        outro = self.survey.get("outro") or "Спасибо за ответы! 🙏"
        await self.channel.send(outro)
        await self.channel.send("_Этот канал будет автоматически удалён через 20 секунд..._")
        await asyncio.sleep(20)
        try:
            await self.channel.delete()
        except discord.HTTPException:
            pass


class SurveyCog(commands.Cog, name="SurveyCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    survey_group = app_commands.Group(name="survey", description="Управление опросами")

    @survey_group.command(name="create", description="Создать опрос из JSON-файла")
    @app_commands.describe(
        name="Короткое имя опроса (латиницей, без пробелов), используется в остальных командах",
        file="JSON-файл с описанием опроса",
    )
    @is_survey_admin()
    async def create(self, interaction: discord.Interaction, name: str, file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        if await self.bot.db.get_survey(name):
            await interaction.followup.send(f"Опрос с именем `{name}` уже существует.", ephemeral=True)
            return
        try:
            raw = await file.read()
            data = json.loads(raw.decode("utf-8"))
            questions = data["questions"]
            validate_questions(questions)
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка в JSON-файле: {e}", ephemeral=True)
            return
        await self.bot.db.create_survey(
            name=name,
            title=data.get("title", name),
            intro=data.get("intro", ""),
            outro=data.get("outro", ""),
            questions=questions,
            anonymous=data.get("anonymous", True),
            allow_multiple=data.get("allow_multiple", False),
            creator_id=interaction.user.id,
        )
        await interaction.followup.send(
            f"✅ Опрос `{name}` создан ({len(questions)} вопрос(ов)).\n"
            f"Опубликуй его командой `/survey publish name:{name}` в нужном канале.",
            ephemeral=True,
        )

    @survey_group.command(name="publish", description="Опубликовать опрос (кнопку) в этом канале")
    @is_survey_admin()
    async def publish(self, interaction: discord.Interaction, name: str):
        survey = await self.bot.db.get_survey(name)
        if not survey:
            await interaction.response.send_message(f"Опрос `{name}` не найден.", ephemeral=True)
            return
        embed = discord.Embed(
            title=survey.get("title") or name,
            description=survey.get("intro") or "",
            color=discord.Color.blurple(),
        )
        view = SurveyStartView(name)
        await interaction.response.send_message(embed=embed, view=view)

    @survey_group.command(name="list", description="Список всех созданных опросов")
    @is_survey_admin()
    async def list_surveys(self, interaction: discord.Interaction):
        surveys = await self.bot.db.list_surveys()
        if not surveys:
            await interaction.response.send_message("Опросов пока нет. Создай через `/survey create`.", ephemeral=True)
            return
        lines = [f"• `{s['name']}` — {s['title']} ({s['question_count']} вопросов)" for s in surveys]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @survey_group.command(name="results", description="Экспортировать ответы опроса в CSV")
    @is_survey_admin()
    async def results(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        survey = await self.bot.db.get_survey(name)
        if not survey:
            await interaction.followup.send(f"Опрос `{name}` не найден.", ephemeral=True)
            return
        responses = await self.bot.db.get_responses(survey["id"])
        if not responses:
            await interaction.followup.send("Пока нет ни одного ответа.", ephemeral=True)
            return
        csv_bytes = build_csv_bytes(survey, responses)
        file = discord.File(io.BytesIO(csv_bytes), filename=f"{name}_results.csv")
        await interaction.followup.send(
            f"📊 Результаты опроса `{name}` ({len(responses)} ответ(ов)):",
            file=file, ephemeral=True,
        )

    @survey_group.command(name="delete", description="Удалить опрос и все его ответы")
    @is_survey_admin()
    async def delete(self, interaction: discord.Interaction, name: str):
        ok = await self.bot.db.delete_survey(name)
        msg = f"🗑️ Опрос `{name}` удалён." if ok else f"Опрос `{name}` не найден."
        await interaction.response.send_message(msg, ephemeral=True)

    @create.error
    @publish.error
    @list_surveys.error
    @results.error
    @delete.error
    async def on_admin_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(str(error), ephemeral=True)
        else:
            log.exception("Command error", exc_info=error)
            if interaction.response.is_done():
                await interaction.followup.send("Произошла непредвиденная ошибка.", ephemeral=True)
            else:
                await interaction.response.send_message("Произошла непредвиденная ошибка.", ephemeral=True)

    async def begin_survey(self, interaction: discord.Interaction, survey_name: str):
        survey = await self.bot.db.get_survey(survey_name)
        if not survey:
            await interaction.response.send_message("Этот опрос больше не существует.", ephemeral=True)
            return
        if not survey["allow_multiple"] and await self.bot.db.has_responded(survey["id"], interaction.user.id):
            await interaction.response.send_message("Ты уже проходил(а) этот опрос. Спасибо! 🙏", ephemeral=True)
            return

        guild = interaction.guild
        category = discord.utils.get(guild.categories, name=config.CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(config.CATEGORY_NAME)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if config.ADMIN_ROLE_ID:
            role = guild.get_role(config.ADMIN_ROLE_ID)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)

        safe_name = "".join(c for c in interaction.user.display_name.lower() if c.isalnum()) or "user"
        channel = await guild.create_text_channel(
            name=f"опрос-{safe_name}"[:90],
            category=category,
            overwrites=overwrites,
        )
        await interaction.response.send_message(f"✅ Опрос открыт в {channel.mention}", ephemeral=True)

        runner = SurveyRunner(self, channel, interaction.user, survey)
        self.bot.loop.create_task(self._run_safely(runner))

    async def _run_safely(self, runner: SurveyRunner):
        try:
            await runner.run()
        except Exception:
            log.exception("Survey run failed")
            try:
                await runner.channel.send("⚠️ Произошла ошибка при прохождении опроса. Обратись к администратору.")
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SurveyCog(bot))
