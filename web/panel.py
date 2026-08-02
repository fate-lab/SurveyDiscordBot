"""
Веб-панель бота: создание опросов и просмотр/отслеживание пройденных опросов.

Работает в ТОМ ЖЕ процессе и на ТОМ ЖЕ порту, что и Discord-бот (это важно
на бесплатных тарифах хостинга вроде Wispbyte, где выделяется только один
внешний порт). Использует ту же базу aiosqlite, что и бот, поэтому данные
всегда синхронизированы.

ВАЖНО: на бесплатном тарифе обычно нет HTTPS, поэтому пароль и данные идут
незашифрованными. Не используйте здесь реально секретную информацию и
задайте длинный случайный WEB_PASSWORD в .env.
"""
import html
import json
import logging
import secrets

from aiohttp import web

import config
from csv_export import build_csv_bytes
from cogs.survey import ALLOWED_TYPES, validate_questions

log = logging.getLogger("survey-bot.web")

COOKIE_NAME = "survey_session"
# Токены сессий живут в памяти процесса — этого достаточно для одной
# небольшой админ-панели и не требует доп. зависимостей/таблиц в БД.
_valid_tokens: set[str] = set()

CSS = """
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    background: #1e1f22; color: #e3e5e8; margin: 0;
  }
  header {
    background: #2b2d31; padding: 14px 24px; display: flex;
    justify-content: space-between; align-items: center;
    border-bottom: 1px solid #3a3c41;
  }
  header a { color: #e3e5e8; text-decoration: none; font-weight: 600; }
  main { max-width: 900px; margin: 24px auto; padding: 0 16px; }
  .card {
    background: #2b2d31; border: 1px solid #3a3c41; border-radius: 10px;
    padding: 20px; margin-bottom: 16px;
  }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #3a3c41; }
  th { color: #9a9ea3; font-weight: 600; font-size: 13px; text-transform: uppercase; }
  a.btn, button, input[type=submit] {
    background: #5865f2; color: white; border: none; border-radius: 6px;
    padding: 8px 14px; cursor: pointer; text-decoration: none; font-size: 14px;
    display: inline-block;
  }
  a.btn.danger, button.danger { background: #da373c; }
  a.btn.secondary, button.secondary { background: #3a3c41; }
  input[type=text], input[type=password], textarea, select {
    width: 100%; padding: 8px 10px; border-radius: 6px; border: 1px solid #3a3c41;
    background: #1e1f22; color: #e3e5e8; margin-top: 4px; margin-bottom: 12px;
  }
  label { font-size: 13px; color: #9a9ea3; }
  .row { display: flex; gap: 10px; }
  .row > * { flex: 1; }
  .q-block { border: 1px solid #3a3c41; border-radius: 8px; padding: 12px; margin-bottom: 10px; }
  .error { color: #f28b82; margin-bottom: 12px; }
  .muted { color: #9a9ea3; font-size: 13px; }
  .pill { background: #3a3c41; border-radius: 999px; padding: 2px 10px; font-size: 12px; }
</style>
"""


def page(title: str, body: str, authed: bool = True) -> str:
    nav = ""
    if authed:
        nav = (
            '<header><a href="/surveys">📋 Опросы (панель)</a>'
            '<form method="post" action="/logout" style="margin:0">'
            '<button class="secondary" type="submit">Выйти</button></form></header>'
        )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>{CSS}</head>
<body>{nav}<main>{body}</main></body></html>"""


def _new_token() -> str:
    token = secrets.token_hex(24)
    _valid_tokens.add(token)
    return token


@web.middleware
async def auth_middleware(request: web.Request, handler):
    open_paths = {"/login"}
    if request.path in open_paths or not config.WEB_PASSWORD:
        return await handler(request)
    token = request.cookies.get(COOKIE_NAME)
    if token not in _valid_tokens:
        raise web.HTTPFound("/login")
    return await handler(request)


async def login_get(request: web.Request):
    if not config.WEB_PASSWORD:
        return web.Response(
            text=page(
                "Панель отключена",
                "<div class='card'><p>WEB_PASSWORD не задан в .env — веб-панель "
                "отключена из соображений безопасности. Задайте пароль и "
                "перезапустите бота.</p></div>",
                authed=False,
            ),
            content_type="text/html",
        )
    error = ""
    if request.query.get("error"):
        error = "<p class='error'>Неверный пароль</p>"
    body = f"""
    <div class="card" style="max-width:360px;margin:60px auto;">
      <h2>Вход в панель опросов</h2>
      {error}
      <form method="post" action="/login">
        <label>Пароль</label>
        <input type="password" name="password" autofocus required>
        <input type="submit" value="Войти">
      </form>
    </div>"""
    return web.Response(text=page("Вход", body, authed=False), content_type="text/html")


async def login_post(request: web.Request):
    data = await request.post()
    if config.WEB_PASSWORD and secrets.compare_digest(
        str(data.get("password", "")), config.WEB_PASSWORD
    ):
        token = _new_token()
        resp = web.HTTPFound("/surveys")
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Lax", max_age=60 * 60 * 12)
        return resp
    return web.HTTPFound("/login?error=1")


async def logout(request: web.Request):
    token = request.cookies.get(COOKIE_NAME)
    _valid_tokens.discard(token)
    resp = web.HTTPFound("/login")
    resp.del_cookie(COOKIE_NAME)
    return resp


async def surveys_list(request: web.Request):
    bot = request.app["bot"]
    surveys = await bot.db.list_surveys()
    rows = "".join(
        f"<tr><td><a href='/surveys/{html.escape(s['name'])}'>{html.escape(s['name'])}</a></td>"
        f"<td>{html.escape(s['title'] or '')}</td>"
        f"<td>{s['question_count']}</td></tr>"
        for s in surveys
    )
    if not rows:
        rows = "<tr><td colspan='3' class='muted'>Опросов пока нет.</td></tr>"
    body = f"""
    <div class="card">
      <div class="row" style="align-items:center;justify-content:space-between;">
        <h2 style="margin:0">Опросы</h2>
        <a class="btn" href="/surveys/new">+ Новый опрос</a>
      </div>
    </div>
    <div class="card">
      <table>
        <tr><th>Имя</th><th>Название</th><th>Вопросов</th></tr>
        {rows}
      </table>
    </div>
    <p class="muted">Публикация кнопки опроса в канал Discord по-прежнему
    делается командой <code>/survey publish name:...</code> — панели не хватает
    контекста "какой сервер / какой канал".</p>
    """
    return web.Response(text=page("Опросы", body), content_type="text/html")


QUESTION_TYPES = list(sorted(ALLOWED_TYPES))

NEW_SURVEY_JS = r"""
<script>
let qCount = 0;
function addQuestion(prefill) {
  prefill = prefill || {};
  const wrap = document.getElementById('questions');
  const idx = qCount++;
  const div = document.createElement('div');
  div.className = 'q-block';
  div.dataset.idx = idx;
  div.innerHTML = `
    <div class="row">
      <div>
        <label>Тип вопроса</label>
        <select class="q-type" onchange="onTypeChange(${idx})">
          __TYPE_OPTIONS__
        </select>
      </div>
      <div>
        <label>Текст вопроса</label>
        <input type="text" class="q-text" required>
      </div>
    </div>
    <div class="q-options-wrap">
      <label>Варианты ответа (по одному на строку, для типов single/multi)</label>
      <textarea class="q-options" rows="3"></textarea>
    </div>
    <div class="row">
      <button type="button" class="secondary" onclick="this.closest('.q-block').remove()">Удалить вопрос</button>
    </div>`;
  wrap.appendChild(div);
}
function onTypeChange(idx) {}
function collectQuestions() {
  const blocks = document.querySelectorAll('.q-block');
  const questions = [];
  blocks.forEach(b => {
    const type = b.querySelector('.q-type').value;
    const text = b.querySelector('.q-text').value.trim();
    if (!text) return;
    const q = { type, text };
    if (type === 'single' || type === 'multi') {
      const opts = b.querySelector('.q-options').value
        .split('\n').map(s => s.trim()).filter(Boolean);
      q.options = opts;
    }
    if (type === 'scale') { q.min = 1; q.max = 5; }
    questions.push(q);
  });
  return questions;
}
function onSubmitForm(e) {
  const questions = collectQuestions();
  if (questions.length === 0) {
    alert('Добавьте хотя бы один вопрос');
    e.preventDefault();
    return false;
  }
  document.getElementById('questions_json').value = JSON.stringify(questions);
  return true;
}
window.addEventListener('DOMContentLoaded', () => addQuestion());
</script>
"""


async def survey_new_get(request: web.Request):
    error = request.query.get("error", "")
    error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
    type_options = "".join(f'<option value="{t}">{t}</option>' for t in QUESTION_TYPES)
    js = NEW_SURVEY_JS.replace("__TYPE_OPTIONS__", type_options)
    body = f"""
    <div class="card">
      <h2>Новый опрос</h2>
      {error_html}
      <form method="post" action="/surveys/new" onsubmit="return onSubmitForm(event)">
        <label>Короткое имя (латиницей, без пробелов, используется в URL)</label>
        <input type="text" name="name" pattern="[A-Za-z0-9_\\-]+" required>
        <label>Заголовок опроса</label>
        <input type="text" name="title" required>
        <label>Вступительный текст (необязательно)</label>
        <textarea name="intro" rows="2"></textarea>
        <label>Текст после завершения (необязательно)</label>
        <textarea name="outro" rows="2"></textarea>
        <div class="row">
          <label><input type="checkbox" name="anonymous" checked style="width:auto"> Анонимный опрос</label>
          <label><input type="checkbox" name="allow_multiple" style="width:auto"> Разрешить проходить повторно</label>
        </div>
        <h3>Вопросы</h3>
        <div id="questions"></div>
        <button type="button" class="secondary" onclick="addQuestion()">+ Добавить вопрос</button>
        <input type="hidden" name="questions_json" id="questions_json">
        <div style="margin-top:16px">
          <input type="submit" value="Создать опрос">
        </div>
      </form>
    </div>
    {js}
    """
    return web.Response(text=page("Новый опрос", body), content_type="text/html")


async def survey_new_post(request: web.Request):
    bot = request.app["bot"]
    data = await request.post()
    name = str(data.get("name", "")).strip()
    title = str(data.get("title", "")).strip() or name
    intro = str(data.get("intro", "")).strip()
    outro = str(data.get("outro", "")).strip()
    anonymous = "anonymous" in data
    allow_multiple = "allow_multiple" in data

    def fail(msg):
        return web.HTTPFound(f"/surveys/new?error={msg}")

    if not name:
        return fail("Укажите короткое имя опроса")
    if await bot.db.get_survey(name):
        return fail(f"Опрос с именем {name} уже существует")
    try:
        questions = json.loads(data.get("questions_json", "[]"))
        validate_questions(questions)
    except Exception as e:
        return fail(f"Ошибка в вопросах: {e}")

    await bot.db.create_survey(
        name=name, title=title, intro=intro, outro=outro,
        questions=questions, anonymous=anonymous, allow_multiple=allow_multiple,
        creator_id=None,
    )
    return web.HTTPFound(f"/surveys/{name}")


async def survey_detail(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    survey = await bot.db.get_survey(name)
    if not survey:
        raise web.HTTPNotFound(text="Опрос не найден")
    responses = await bot.db.get_responses(survey["id"])
    rows = []
    for i, r in enumerate(responses, start=1):
        who = f"Аноним #{i}" if survey["anonymous"] else str(r["user_id"])
        rows.append(
            f"<tr><td>{html.escape(who)}</td><td>{html.escape(r['submitted_at'])}</td></tr>"
        )
    rows_html = "".join(rows) or "<tr><td colspan='2' class='muted'>Ответов пока нет.</td></tr>"
    body = f"""
    <div class="card">
      <div class="row" style="align-items:center;justify-content:space-between;">
        <h2 style="margin:0">{html.escape(survey.get('title') or name)}
          <span class="pill">{len(responses)} прош{'ёл' if len(responses)==1 else 'ло'}</span></h2>
        <div class="row" style="flex:none;gap:8px">
          <a class="btn" href="/surveys/{html.escape(name)}/export.csv">Скачать CSV</a>
          <form method="post" action="/surveys/{html.escape(name)}/delete"
                onsubmit="return confirm('Удалить опрос и все ответы?')">
            <button class="danger" type="submit">Удалить</button>
          </form>
        </div>
      </div>
      <p class="muted">Имя: <code>{html.escape(name)}</code> ·
      {len(survey['questions'])} вопрос(ов) ·
      {'анонимный' if survey['anonymous'] else 'с указанием пользователя'}</p>
    </div>
    <div class="card">
      <h3>Кто прошёл опрос</h3>
      <table>
        <tr><th>Респондент</th><th>Дата (UTC)</th></tr>
        {rows_html}
      </table>
    </div>
    <p class="muted">Опубликовать кнопку опроса в Discord-канал:
    <code>/survey publish name:{html.escape(name)}</code></p>
    """
    return web.Response(text=page(survey.get("title") or name, body), content_type="text/html")


async def survey_export_csv(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    survey = await bot.db.get_survey(name)
    if not survey:
        raise web.HTTPNotFound(text="Опрос не найден")
    responses = await bot.db.get_responses(survey["id"])
    csv_bytes = build_csv_bytes(survey, responses)
    return web.Response(
        body=csv_bytes,
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}_results.csv"'},
    )


async def survey_delete(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    await bot.db.delete_survey(name)
    return web.HTTPFound("/surveys")


async def index(request: web.Request):
    return web.HTTPFound("/surveys")


def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["bot"] = bot
    app.router.add_get("/", index)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_post("/logout", logout)
    app.router.add_get("/surveys", surveys_list)
    app.router.add_get("/surveys/new", survey_new_get)
    app.router.add_post("/surveys/new", survey_new_post)
    app.router.add_get("/surveys/{name}", survey_detail)
    app.router.add_get("/surveys/{name}/export.csv", survey_export_csv)
    app.router.add_post("/surveys/{name}/delete", survey_delete)
    return app


async def start_web_panel(bot):
    if not config.WEB_ENABLED:
        log.info("Веб-панель отключена (WEB_ENABLED=0)")
        return
    if not config.WEB_PASSWORD:
        log.warning(
            "WEB_PASSWORD не задан в .env — веб-панель будет отвечать 'отключена'. "
            "Задайте WEB_PASSWORD, чтобы включить её."
        )
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    log.info(f"Веб-панель запущена на {config.WEB_HOST}:{config.WEB_PORT}")
