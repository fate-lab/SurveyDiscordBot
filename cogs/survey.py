import asyncio
import io
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from csv_export import build_csv_bytes
from i18n import t, loc, has_any_lang, DEFAULT_LANG, SUPPORTED_LANGS

log = logging.getLogger("survey-bot")

ALLOWED_TYPES = {"scale", "single", "multi", "yes_no_detail", "text"}


def _text_len(value) -> int:
    """Длина текста для проверок/обрезки — работает и со строкой, и с {ru,en}."""
    if isinstance(value, dict):
        return max(len(str(value.get("ru") or "")), len(str(value.get("en") or "")))
    return len(str(value or ""))


def validate_questions(questions):
    """Валидирует список вопросов. Каждое текстовое поле (text, options,
    other_label, min_label/max_label, yes_label/no_label) может быть либо
    обычной строкой/списком (старый одноязычный формат), либо словарём
    {"ru": ..., "en": ...} (новый двуязычный формат из веб-панели). Хотя бы
    один из двух языков должен быть заполнен — второй можно дописать позже."""
    if not isinstance(questions, list) or not questions:
        raise ValueError("Поле 'questions' должно быть непустым списком.")
    for i, q in enumerate(questions):
        if "type" not in q or q["type"] not in ALLOWED_TYPES:
            raise ValueError(
                f"Вопрос {i + 1}: неизвестный или отсутствующий 'type'. "
                f"Допустимые типы: {', '.join(sorted(ALLOWED_TYPES))}"
            )
        if "text" not in q or not has_any_lang(q["text"]):
            raise ValueError(f"Вопрос {i + 1}: отсутствует 'text' (заполните хотя бы один язык)")
        if q["type"] in ("single", "multi") and not has_any_lang(q.get("options")):
            raise ValueError(f"Вопрос {i + 1}: для типа '{q['type']}' нужен непустой список 'options'")
        if q["type"] == "scale":
            q.setdefault("min", 1)
            q.setdefault("max", 5)
        # other_label — необязательное переопределение подписи кнопки/пункта
        # "Другое"/"Other" для конкретного вопроса (иначе берётся из языка юзера)
        if "other_label" in q and not has_any_lang(q["other_label"]):
            q.pop("other_label", None)


def build_bilingual_embed(survey: dict, name: str) -> discord.Embed:
    """Карточка-приглашение видят все сразу (это не личное сообщение), поэтому
    вместо подбора языка под конкретного человека показываем сразу оба —
    RU и EN, — а не только "язык опроса", как было раньше."""
    title_ru = loc(survey.get("title") or name, "ru")
    title_en = loc(survey.get("title") or name, "en")
    if title_ru and title_en and title_ru != title_en:
        title = f"{title_ru} / {title_en}"
    else:
        title = title_ru or title_en or name

    intro_ru = loc(survey.get("intro"), "ru")
    intro_en = loc(survey.get("intro"), "en")
    parts = []
    if intro_ru:
        parts.append(f"🇷🇺 {intro_ru}")
    if intro_en and intro_en != intro_ru:
        parts.append(f"🇬🇧 {intro_en}")
    return discord.Embed(title=title, description="\n\n".join(parts), color=discord.Color.blurple())


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
    def __init__(self, title: str, label: str, long: bool = False, lang: str = DEFAULT_LANG):
        super().__init__(title=title[:45])
        self.value = None
        self.lang = lang
        style = discord.TextStyle.paragraph if long else discord.TextStyle.short
        self.input = discord.ui.TextInput(label=label[:45], style=style, required=True, max_length=1000)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        self.value = str(self.input.value)
        await interaction.response.send_message(t(self.lang, "answer_received"), ephemeral=True)
        self.stop()


BILINGUAL_TAKE_SURVEY_LABEL = "📋 Пройти опрос / Take survey"
BILINGUAL_LANG_PROMPT = (
    "🌐 Перед началом выбери язык опроса — это займёт секунду и запомнится "
    "для всех следующих опросов.\n"
    "🌐 Before we start, pick your language — takes a second and will be "
    "remembered for future surveys."
)


class SurveyStartView(discord.ui.View):
    """Persistent view attached to the published survey message."""

    def __init__(self, survey_name: str, label: str = BILINGUAL_TAKE_SURVEY_LABEL):
        super().__init__(timeout=None)
        self.survey_name = survey_name
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"survey_start:{survey_name}",
        )
        button.callback = self.start_survey
        self.add_item(button)

    async def start_survey(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("SurveyCog")
        if cog is None:
            await interaction.response.send_message(t(DEFAULT_LANG, "bot_restarting"), ephemeral=True)
            return
        await cog.begin_survey(interaction, self.survey_name)


class SurveyLangPromptView(discord.ui.View):
    """Показывается один раз тому, кто ещё ни разу не выбирал язык —
    аналог настройки языка (как в /language setup), но прямо перед стартом
    опроса, чтобы не заставлять сначала идти в отдельный канал."""

    def __init__(self, cog, survey_name: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.survey_name = survey_name

    @discord.ui.button(label="English", emoji="🇬🇧", style=discord.ButtonStyle.primary)
    async def pick_en(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "en")

    @discord.ui.button(label="Русский", emoji="🇷🇺", style=discord.ButtonStyle.secondary)
    async def pick_ru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "ru")

    async def _pick(self, interaction: discord.Interaction, lang: str):
        await interaction.client.db.set_user_lang(interaction.user.id, lang)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=t(lang, "answer_received"), view=self)
        await self.cog.open_survey_channel(interaction, self.survey_name, lang, use_followup=True)


class SurveyRunner:
    """Walks a single respondent through all questions inside their private channel."""

    def __init__(self, cog, channel: discord.TextChannel, member: discord.Member, survey: dict, lang: str):
        self.cog = cog
        self.channel = channel
        self.member = member
        self.survey = survey
        # Язык самого проходящего (выбранный им лично), а не "язык опроса" —
        # опрос теперь двуязычный, показываем каждому на его языке.
        self.lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
        self.answers = {}

    async def run(self):
        for idx, q in enumerate(self.survey["questions"]):
            answer = await self.ask(idx, q)
            self.answers[str(idx)] = {"question": loc(q["text"], self.lang), "answer": answer}
        await self.finish()

    def _q_text(self, q):
        return loc(q["text"], self.lang)

    def _q_options(self, q):
        opts = loc(q.get("options"), self.lang)
        return list(opts) if opts else []

    def _other_label(self, q):
        return loc(q.get("other_label"), self.lang) or t(self.lang, "other_option_label")

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
            result = t(self.lang, "timeout_answer")
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
        min_label = loc(q.get("min_label"), self.lang)
        max_label = loc(q.get("max_label"), self.lang)
        hint = ""
        if min_label or max_label:
            hint = f"\n_(​{lo} — {min_label}, {hi} — {max_label})_"
        msg = await self.channel.send(f"**{idx + 1}. {self._q_text(q)}**{hint}", view=view)
        return await self._wait(future, view, msg)

    async def _ask_single(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        options = self._q_options(q)
        has_other = q.get("other_option", False)
        other_label = self._other_label(q)
        select_options = [discord.SelectOption(label=o[:100], value=o) for o in options]
        if has_other:
            select_options.append(discord.SelectOption(label=other_label[:100], value="__other__"))
        view = discord.ui.View(timeout=600)
        select = discord.ui.Select(placeholder=t(self.lang, "select_single_placeholder"),
                                    options=select_options, min_values=1, max_values=1)

        async def cb(interaction):
            value = select.values[0]
            if value == "__other__":
                modal = FreeTextModal(title=other_label, label=t(self.lang, "other_modal_label"), lang=self.lang)
                await interaction.response.send_modal(modal)
                await modal.wait()
                if not future.done():
                    future.set_result(f"{other_label}: {modal.value or ''}")
            else:
                await interaction.response.defer()
                if not future.done():
                    future.set_result(value)

        select.callback = cb
        view.add_item(select)
        msg = await self.channel.send(f"**{idx + 1}. {self._q_text(q)}**", view=view)
        return await self._wait(future, view, msg)

    async def _ask_multi(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        options = self._q_options(q)
        has_other = q.get("other_option", False)
        other_label = self._other_label(q)
        select_options = [discord.SelectOption(label=o[:100], value=o) for o in options]
        if has_other:
            select_options.append(discord.SelectOption(label=other_label[:100], value="__other__"))
        view = discord.ui.View(timeout=600)
        select = discord.ui.Select(
            placeholder=t(self.lang, "select_multi_placeholder"),
            options=select_options, min_values=1, max_values=len(select_options),
        )
        confirm_btn = discord.ui.Button(label=t(self.lang, "done_button"), style=discord.ButtonStyle.success)
        state = {"values": []}

        async def select_cb(interaction):
            state["values"] = list(select.values)
            await interaction.response.defer()

        async def confirm_cb(interaction):
            values = state["values"]
            if not values:
                await interaction.response.send_message(t(self.lang, "multi_need_one"), ephemeral=True)
                return
            if "__other__" in values:
                modal = FreeTextModal(title=other_label, label=t(self.lang, "other_modal_label"), lang=self.lang)
                await interaction.response.send_modal(modal)
                await modal.wait()
                values = [v for v in values if v != "__other__"]
                if modal.value:
                    values.append(f"{other_label}: {modal.value}")
            else:
                await interaction.response.defer()
            if not future.done():
                future.set_result(", ".join(values))

        select.callback = select_cb
        confirm_btn.callback = confirm_cb
        view.add_item(select)
        view.add_item(confirm_btn)
        hint = t(self.lang, "multi_hint")
        msg = await self.channel.send(f"**{idx + 1}. {self._q_text(q)}**{hint}", view=view)
        return await self._wait(future, view, msg)

    async def _ask_yes_no_detail(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        view = discord.ui.View(timeout=600)
        yes_label = loc(q.get("yes_label"), self.lang) or t(self.lang, "yes_label_default")
        no_label = loc(q.get("no_label"), self.lang) or t(self.lang, "no_label_default")
        yes_btn = discord.ui.Button(label=yes_label, style=discord.ButtonStyle.success)
        no_btn = discord.ui.Button(label=no_label, style=discord.ButtonStyle.secondary)

        async def yes_cb(interaction):
            modal = FreeTextModal(
                title=t(self.lang, "yes_modal_title"), label=t(self.lang, "yes_modal_label"),
                long=True, lang=self.lang,
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            if not future.done():
                future.set_result(f"{yes_label}: {modal.value or ''}")

        async def no_cb(interaction):
            await interaction.response.defer()
            if not future.done():
                future.set_result(no_label)

        yes_btn.callback = yes_cb
        no_btn.callback = no_cb
        view.add_item(yes_btn)
        view.add_item(no_btn)
        msg = await self.channel.send(f"**{idx + 1}. {self._q_text(q)}**", view=view)
        return await self._wait(future, view, msg)

    async def _ask_text(self, idx, q):
        future = asyncio.get_event_loop().create_future()
        view = discord.ui.View(timeout=600)
        btn = discord.ui.Button(label=t(self.lang, "answer_button"), style=discord.ButtonStyle.primary)
        q_text = self._q_text(q)

        async def cb(interaction):
            modal = FreeTextModal(
                title=t(self.lang, "answer_modal_title"), label=q_text[:45],
                long=True, lang=self.lang,
            )
            await interaction.response.send_modal(modal)
            await modal.wait()
            if not future.done():
                future.set_result(modal.value or "")

        btn.callback = cb
        view.add_item(btn)
        msg = await self.channel.send(f"**{idx + 1}. {q_text}**", view=view)
        return await self._wait(future, view, msg)

    async def finish(self):
        survey = await self.cog.bot.db.get_survey(self.survey["name"])
        await self.cog.bot.db.save_response(survey["id"], self.member.id, self.answers)
        outro = loc(self.survey.get("outro"), self.lang) or t(self.lang, "default_outro")
        await self.channel.send(outro)
        await self.channel.send(t(self.lang, "channel_autodelete"))
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
            lang = data.get("lang", DEFAULT_LANG)
            if lang not in SUPPORTED_LANGS:
                raise ValueError(f"'lang' должен быть одним из {SUPPORTED_LANGS}, получено '{lang}'")
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
            lang=lang,
        )
        await interaction.followup.send(
            f"✅ Опрос `{name}` создан ({len(questions)} вопрос(ов), язык: {lang}).\n"
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
        embed = build_bilingual_embed(survey, name)
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
            await interaction.response.send_message(t(DEFAULT_LANG, "survey_gone"), ephemeral=True)
            return

        # Если человек ещё ни разу не выбирал язык (ни через /language, ни
        # в предыдущем опросе) — спрашиваем прямо тут, один раз, как мини-настройка,
        # и только после выбора открываем канал с опросом.
        existing_lang = await self.bot.db.get_user_lang_raw(interaction.user.id)
        if existing_lang is None:
            await interaction.response.send_message(
                BILINGUAL_LANG_PROMPT,
                view=SurveyLangPromptView(self, survey_name),
                ephemeral=True,
            )
            return

        # already_responded / allow_multiple проверяем уже на известном языке
        if not survey["allow_multiple"] and await self.bot.db.has_responded(survey["id"], interaction.user.id):
            await interaction.response.send_message(t(existing_lang, "already_responded"), ephemeral=True)
            return

        await self.open_survey_channel(interaction, survey_name, existing_lang, use_followup=False)

    async def open_survey_channel(self, interaction: discord.Interaction, survey_name: str,
                                   lang: str, use_followup: bool):
        """Создаёт приватный канал и запускает прохождение опроса на выбранном
        языке. use_followup=True — когда мы уже ответили на interaction раньше
        (после выбора языка кнопкой) и теперь должны использовать followup."""
        survey = await self.bot.db.get_survey(survey_name)
        if not survey:
            send = interaction.followup.send if use_followup else interaction.response.send_message
            await send(t(lang, "survey_gone"), ephemeral=True)
            return
        if not survey["allow_multiple"] and await self.bot.db.has_responded(survey["id"], interaction.user.id):
            send = interaction.followup.send if use_followup else interaction.response.send_message
            await send(t(lang, "already_responded"), ephemeral=True)
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
        prefix = t(lang, "channel_prefix")
        channel = await guild.create_text_channel(
            name=f"{prefix}{safe_name}"[:90],
            category=category,
            overwrites=overwrites,
        )
        msg = t(lang, "opened_in_channel", channel=channel.mention)
        if use_followup:
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

        runner = SurveyRunner(self, channel, interaction.user, survey, lang)
        self.bot.loop.create_task(self._run_safely(runner))

    async def _run_safely(self, runner: SurveyRunner):
        try:
            await runner.run()
        except Exception:
            log.exception("Survey run failed")
            try:
                await runner.channel.send(t(runner.lang, "run_error"))
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SurveyCog(bot))
