"""
Веб-панель бота: создание/редактирование/клонирование опросов и просмотр
кто их прошёл. Работает в ТОМ ЖЕ процессе и на ТОМ ЖЕ порту, что и
Discord-бот (важно на бесплатных хостингах вроде Wispbyte, где выделяется
только один внешний порт). Использует ту же базу aiosqlite, что и бот.

ВАЖНО: на бесплатном тарифе обычно нет HTTPS, поэтому пароль и данные идут
незашифрованными. Задайте длинный случайный WEB_PASSWORD в .env.
"""
import html
import json
import logging
import secrets

from aiohttp import web

import config
from csv_export import build_csv_bytes
from cogs.survey import ALLOWED_TYPES, validate_questions
from i18n import t as tr, DEFAULT_LANG, SUPPORTED_LANGS

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
  input[type=text], input[type=password], input[type=number],
  textarea, select {
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
  code.copyline {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: #1e1f22; border: 1px solid #3a3c41; border-radius: 6px;
    padding: 10px 12px; font-size: 14px;
  }
  code.copyline button { flex: none; padding: 4px 10px; font-size: 12px; }
  details.q-block summary { list-style: none; }
  details.q-block summary::-webkit-details-marker { display: none; }
  details.q-block summary::before { content: "▶"; margin-right: 8px; font-size: 11px; color: #9a9ea3; }
  details.q-block[open] summary::before { content: "▼"; }
  details.q-block table td { border-bottom: 1px solid #26282c; }
  details.q-block table tr:last-child td { border-bottom: none; }
</style>
"""


def page(title: str, body: str, authed: bool = True) -> str:
    nav = ""
    if authed:
        nav = (
            '<header><div style="display:flex;gap:16px;">'
            '<a href="/surveys">📋 Опросы</a>'
            '<a href="/events">🎫 События</a>'
            '</div>'
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


LANG_NAMES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}


def _field_has(value, lang) -> bool:
    if isinstance(value, dict):
        return bool(str(value.get(lang) or "").strip())
    # старый одноязычный опрос — считаем, что заполнен на его "родном" lang
    return bool(str(value or "").strip())


def lang_badge(survey: dict) -> str:
    """Показывает, на каких языках реально есть контент (заголовок опроса),
    а не формальное поле 'lang', которое раньше значило 'единственный язык'."""
    title = survey.get("title")
    if isinstance(title, dict):
        ru_ok, en_ok = _field_has(title, "ru"), _field_has(title, "en")
    else:
        # Старый формат: единственный язык хранился в отдельной колонке lang
        old_lang = survey.get("lang") or DEFAULT_LANG
        ru_ok, en_ok = old_lang == "ru", old_lang == "en"
    if ru_ok and en_ok:
        return "🇷🇺 + 🇬🇧"
    if ru_ok:
        return "🇷🇺 только"
    if en_ok:
        return "🇬🇧 only"
    return "—"


def title_str(survey: dict) -> str:
    title = survey.get("title")
    if isinstance(title, dict):
        ru, en = title.get("ru") or "", title.get("en") or ""
        if ru and en and ru != en:
            return f"{ru} / {en}"
        return ru or en
    return title or ""


async def surveys_list(request: web.Request):
    bot = request.app["bot"]
    surveys = await bot.db.list_surveys()
    rows = "".join(
        f"<tr><td><a href='/surveys/{html.escape(s['name'])}'>{html.escape(s['name'])}</a></td>"
        f"<td>{html.escape(title_str(s))}</td>"
        f"<td>{s['question_count']}</td>"
        f"<td>{lang_badge(s)}</td></tr>"
        for s in surveys
    )
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>Опросов пока нет.</td></tr>"
    body = f"""
    <div class="card">
      <div class="row" style="align-items:center;justify-content:space-between;">
        <h2 style="margin:0">Опросы</h2>
        <a class="btn" href="/surveys/new">+ Новый опрос</a>
      </div>
    </div>
    <div class="card">
      <table>
        <tr><th>Имя</th><th>Название</th><th>Вопросов</th><th>Язык</th></tr>
        {rows}
      </table>
    </div>
    """
    return web.Response(text=page("Опросы", body), content_type="text/html")


QUESTION_TYPES = list(sorted(ALLOWED_TYPES))

# Каждое текстовое поле вопроса теперь вводится в двух колонках — 🇷🇺 и 🇬🇧.
# Можно заполнить только один язык (второй возьмётся автоматически как
# запасной при прохождении опроса), поэтому required тут не ставим — валидация
# "хотя бы один язык" делается на сервере в validate_questions().
FORM_JS = r"""
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
        <select class="q-type" onchange="onTypeChange(this)">
          __TYPE_OPTIONS__
        </select>
      </div>
    </div>
    <div class="row">
      <div>
        <label>🇷🇺 Текст вопроса</label>
        <input type="text" class="q-text-ru">
      </div>
      <div>
        <label>🇬🇧 Question text</label>
        <input type="text" class="q-text-en">
      </div>
    </div>

    <div class="q-scale-wrap" style="display:none">
      <div class="row">
        <div>
          <label>Мин. значение</label>
          <input type="number" class="q-min" value="1">
        </div>
        <div>
          <label>Макс. значение</label>
          <input type="number" class="q-max" value="5">
        </div>
      </div>
      <div class="row">
        <div>
          <label>🇷🇺 Подпись у минимума (необязательно)</label>
          <input type="text" class="q-min-label-ru" placeholder="например: плохо">
        </div>
        <div>
          <label>🇬🇧 Min label (optional)</label>
          <input type="text" class="q-min-label-en" placeholder="e.g. bad">
        </div>
      </div>
      <div class="row">
        <div>
          <label>🇷🇺 Подпись у максимума (необязательно)</label>
          <input type="text" class="q-max-label-ru" placeholder="например: отлично">
        </div>
        <div>
          <label>🇬🇧 Max label (optional)</label>
          <input type="text" class="q-max-label-en" placeholder="e.g. great">
        </div>
      </div>
    </div>

    <div class="q-options-wrap" style="display:none">
      <div class="row">
        <div>
          <label>🇷🇺 Варианты ответа (по одному на строку)</label>
          <textarea class="q-options-ru" rows="3"></textarea>
        </div>
        <div>
          <label>🇬🇧 Options (one per line)</label>
          <textarea class="q-options-en" rows="3"></textarea>
        </div>
      </div>
      <p class="muted" style="margin-top:-6px">Важно: количество строк и их порядок должны
      совпадать в обеих колонках — это перевод одних и тех же вариантов, а не разные списки.</p>
      <label><input type="checkbox" class="q-other" style="width:auto" onchange="onOtherToggle(this)"> Добавить вариант "Другое" / "Other"</label>
      <div class="q-other-label-wrap" style="display:none">
        <div class="row">
          <div>
            <label>🇷🇺 Своя подпись (необязательно, иначе "Другое")</label>
            <input type="text" class="q-other-label-ru" placeholder="например: Свой вариант">
          </div>
          <div>
            <label>🇬🇧 Custom label (optional, else "Other")</label>
            <input type="text" class="q-other-label-en" placeholder="e.g. My own answer">
          </div>
        </div>
      </div>
    </div>

    <div class="q-yesno-wrap" style="display:none">
      <div class="row">
        <div>
          <label>🇷🇺 Подпись на кнопке "Да" (необязательно)</label>
          <input type="text" class="q-yes-label-ru" placeholder="Да">
        </div>
        <div>
          <label>🇬🇧 "Yes" button label (optional)</label>
          <input type="text" class="q-yes-label-en" placeholder="Yes">
        </div>
      </div>
      <div class="row">
        <div>
          <label>🇷🇺 Подпись на кнопке "Нет" (необязательно)</label>
          <input type="text" class="q-no-label-ru" placeholder="Нет">
        </div>
        <div>
          <label>🇬🇧 "No" button label (optional)</label>
          <input type="text" class="q-no-label-en" placeholder="No">
        </div>
      </div>
    </div>

    <div class="row">
      <button type="button" class="secondary" onclick="this.closest('.q-block').remove()">Удалить вопрос</button>
    </div>`;
  wrap.appendChild(div);

  // применяем prefill (используется при редактировании существующего опроса).
  // Поля могут прийти как строка (старый одноязычный опрос) или объект {ru,en}.
  const asObj = (v) => (v && typeof v === 'object') ? v : { ru: v || '', en: '' };
  const set = (sel, val) => { const el = div.querySelector(sel); if (el && val) el.value = val; };

  const typeSel = div.querySelector('.q-type');
  if (prefill.type) typeSel.value = prefill.type;
  const text = asObj(prefill.text);
  set('.q-text-ru', text.ru); set('.q-text-en', text.en);

  if (prefill.options) {
    const opts = asObj(prefill.options);
    if (Array.isArray(opts)) { set('.q-options-ru', opts.join('\n')); }
    else {
      set('.q-options-ru', (opts.ru || []).join('\n'));
      set('.q-options-en', (opts.en || []).join('\n'));
    }
  }
  if (prefill.other_option) {
    div.querySelector('.q-other').checked = true;
    div.querySelector('.q-other-label-wrap').style.display = '';
  }
  if (prefill.other_label) {
    const ol = asObj(prefill.other_label);
    set('.q-other-label-ru', ol.ru); set('.q-other-label-en', ol.en);
  }
  if (prefill.min !== undefined) div.querySelector('.q-min').value = prefill.min;
  if (prefill.max !== undefined) div.querySelector('.q-max').value = prefill.max;
  if (prefill.min_label) { const l = asObj(prefill.min_label); set('.q-min-label-ru', l.ru); set('.q-min-label-en', l.en); }
  if (prefill.max_label) { const l = asObj(prefill.max_label); set('.q-max-label-ru', l.ru); set('.q-max-label-en', l.en); }
  if (prefill.yes_label) { const l = asObj(prefill.yes_label); set('.q-yes-label-ru', l.ru); set('.q-yes-label-en', l.en); }
  if (prefill.no_label) { const l = asObj(prefill.no_label); set('.q-no-label-ru', l.ru); set('.q-no-label-en', l.en); }
  onTypeChange(typeSel);
}
function onTypeChange(select) {
  const b = select.closest('.q-block');
  const type = select.value;
  b.querySelector('.q-scale-wrap').style.display = (type === 'scale') ? '' : 'none';
  b.querySelector('.q-options-wrap').style.display = (type === 'single' || type === 'multi') ? '' : 'none';
  b.querySelector('.q-yesno-wrap').style.display = (type === 'yes_no_detail') ? '' : 'none';
}
function onOtherToggle(checkbox) {
  const wrap = checkbox.closest('.q-options-wrap').querySelector('.q-other-label-wrap');
  wrap.style.display = checkbox.checked ? '' : 'none';
}
function bilingual(b, ruSel, enSel) {
  const ru = (b.querySelector(ruSel).value || '').trim();
  const en = (b.querySelector(enSel).value || '').trim();
  return { ru, en };
}
function bilingualOrUndefined(b, ruSel, enSel) {
  const v = bilingual(b, ruSel, enSel);
  return (v.ru || v.en) ? v : undefined;
}
function collectQuestions() {
  const blocks = document.querySelectorAll('.q-block');
  const questions = [];
  blocks.forEach(b => {
    const type = b.querySelector('.q-type').value;
    const text = bilingual(b, '.q-text-ru', '.q-text-en');
    if (!text.ru && !text.en) return;
    const q = { type, text };
    if (type === 'single' || type === 'multi') {
      const optsRu = b.querySelector('.q-options-ru').value.split('\n').map(s => s.trim()).filter(Boolean);
      const optsEn = b.querySelector('.q-options-en').value.split('\n').map(s => s.trim()).filter(Boolean);
      q.options = { ru: optsRu, en: optsEn };
      if (b.querySelector('.q-other').checked) {
        q.other_option = true;
        const otherLabel = bilingualOrUndefined(b, '.q-other-label-ru', '.q-other-label-en');
        if (otherLabel) q.other_label = otherLabel;
      }
    }
    if (type === 'scale') {
      q.min = parseInt(b.querySelector('.q-min').value || '1', 10);
      q.max = parseInt(b.querySelector('.q-max').value || '5', 10);
      const minLabel = bilingualOrUndefined(b, '.q-min-label-ru', '.q-min-label-en');
      const maxLabel = bilingualOrUndefined(b, '.q-max-label-ru', '.q-max-label-en');
      if (minLabel) q.min_label = minLabel;
      if (maxLabel) q.max_label = maxLabel;
    }
    if (type === 'yes_no_detail') {
      const yesLabel = bilingualOrUndefined(b, '.q-yes-label-ru', '.q-yes-label-en');
      const noLabel = bilingualOrUndefined(b, '.q-no-label-ru', '.q-no-label-en');
      if (yesLabel) q.yes_label = yesLabel;
      if (noLabel) q.no_label = noLabel;
    }
    questions.push(q);
  });
  return questions;
}
function onSubmitForm(e) {
  const questions = collectQuestions();
  if (questions.length === 0) {
    alert('Добавьте хотя бы один вопрос (текст хотя бы на одном языке)');
    e.preventDefault();
    return false;
  }
  document.getElementById('questions_json').value = JSON.stringify(questions);
  return true;
}
"""


def _bilingual_of(value) -> dict:
    """Приводит title/intro/outro (строка или {ru,en}) к словарю для формы."""
    if isinstance(value, dict):
        return {"ru": value.get("ru") or "", "en": value.get("en") or ""}
    return {"ru": value or "", "en": ""}


def _survey_form_body(*, action_url: str, submit_label: str, error: str = "",
                       prefill_questions_json: str = "[]",
                       name_value: str = "", name_readonly: bool = False,
                       title_value=None, intro_value=None, outro_value=None,
                       anonymous_checked: bool = True, allow_multiple_checked: bool = False) -> str:
    error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
    type_options = "".join(f'<option value="{qt}">{qt}</option>' for qt in QUESTION_TYPES)
    name_attr = 'readonly style="opacity:0.7"' if name_readonly else 'pattern="[A-Za-z0-9_\\-]+"'
    js = FORM_JS.replace("__TYPE_OPTIONS__", type_options)
    title = _bilingual_of(title_value)
    intro = _bilingual_of(intro_value)
    outro = _bilingual_of(outro_value)
    return f"""
    <div class="card">
      <h2>{submit_label}</h2>
      {error_html}
      <p class="muted">Опрос двуязычный: заполни колонку 🇷🇺 и/или 🇬🇧 для каждого поля.
      Кто проходит опрос — увидит текст на своём языке (выбранном в Discord);
      если для его языка перевод не заполнен, покажется второй язык.</p>
      <form method="post" action="{action_url}" onsubmit="return onSubmitForm(event)">
        <label>Короткое имя (латиницей, без пробелов, используется в URL)</label>
        <input type="text" name="name" value="{html.escape(name_value)}" {name_attr} required>

        <label>Заголовок опроса</label>
        <div class="row">
          <div><label class="muted">🇷🇺 RU</label><input type="text" name="title_ru" value="{html.escape(title['ru'])}"></div>
          <div><label class="muted">🇬🇧 EN</label><input type="text" name="title_en" value="{html.escape(title['en'])}"></div>
        </div>

        <label>Вступительный текст (необязательно)</label>
        <div class="row">
          <div><label class="muted">🇷🇺 RU</label><textarea name="intro_ru" rows="2">{html.escape(intro['ru'])}</textarea></div>
          <div><label class="muted">🇬🇧 EN</label><textarea name="intro_en" rows="2">{html.escape(intro['en'])}</textarea></div>
        </div>

        <label>Текст после завершения (необязательно)</label>
        <div class="row">
          <div><label class="muted">🇷🇺 RU</label><textarea name="outro_ru" rows="2">{html.escape(outro['ru'])}</textarea></div>
          <div><label class="muted">🇬🇧 EN</label><textarea name="outro_en" rows="2">{html.escape(outro['en'])}</textarea></div>
        </div>

        <div class="row">
          <label><input type="checkbox" name="anonymous" {"checked" if anonymous_checked else ""} style="width:auto"> Анонимный опрос</label>
          <label><input type="checkbox" name="allow_multiple" {"checked" if allow_multiple_checked else ""} style="width:auto"> Разрешить проходить повторно</label>
        </div>
        <h3>Вопросы</h3>
        <div id="questions"></div>
        <button type="button" class="secondary" onclick="addQuestion()">+ Добавить вопрос</button>
        <input type="hidden" name="questions_json" id="questions_json">
        <div style="margin-top:16px">
          <input type="submit" value="{submit_label}">
        </div>
      </form>
    </div>
    <script>
    {js}
    const __prefillQuestions = {prefill_questions_json};
    if (__prefillQuestions.length) {{
      __prefillQuestions.forEach(q => addQuestion(q));
    }} else {{
      addQuestion();
    }}
    </script>
    """


async def survey_new_get(request: web.Request):
    error = request.query.get("error", "")
    body = _survey_form_body(
        action_url="/surveys/new", submit_label="Создать опрос", error=error,
    )
    return web.Response(text=page("Новый опрос", body), content_type="text/html")


def _bilingual_from_form(data, base_name: str) -> dict:
    ru = str(data.get(f"{base_name}_ru", "")).strip()
    en = str(data.get(f"{base_name}_en", "")).strip()
    return {"ru": ru, "en": en}


async def survey_new_post(request: web.Request):
    bot = request.app["bot"]
    data = await request.post()
    name = str(data.get("name", "")).strip()
    title = _bilingual_from_form(data, "title")
    if not title["ru"] and not title["en"]:
        title = {"ru": name, "en": ""}
    intro = _bilingual_from_form(data, "intro")
    outro = _bilingual_from_form(data, "outro")
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
        creator_id=None, lang=DEFAULT_LANG,
    )
    return web.HTTPFound(f"/surveys/{name}")


async def survey_edit_get(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    survey = await bot.db.get_survey(name)
    if not survey:
        raise web.HTTPNotFound(text="Опрос не найден")
    error = request.query.get("error", "")
    body = _survey_form_body(
        action_url=f"/surveys/{name}/edit", submit_label="Сохранить изменения", error=error,
        prefill_questions_json=json.dumps(survey["questions"], ensure_ascii=False),
        name_value=name, name_readonly=True,
        title_value=survey.get("title"), intro_value=survey.get("intro"),
        outro_value=survey.get("outro"),
        anonymous_checked=survey["anonymous"], allow_multiple_checked=survey["allow_multiple"],
    )
    return web.Response(text=page(f"Редактирование: {name}", body), content_type="text/html")


async def survey_edit_post(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    if not await bot.db.get_survey(name):
        raise web.HTTPNotFound(text="Опрос не найден")
    data = await request.post()
    title = _bilingual_from_form(data, "title")
    if not title["ru"] and not title["en"]:
        title = {"ru": name, "en": ""}
    intro = _bilingual_from_form(data, "intro")
    outro = _bilingual_from_form(data, "outro")
    anonymous = "anonymous" in data
    allow_multiple = "allow_multiple" in data

    def fail(msg):
        return web.HTTPFound(f"/surveys/{name}/edit?error={msg}")

    try:
        questions = json.loads(data.get("questions_json", "[]"))
        validate_questions(questions)
    except Exception as e:
        return fail(f"Ошибка в вопросах: {e}")

    await bot.db.update_survey(
        name=name, title=title, intro=intro, outro=outro,
        questions=questions, anonymous=anonymous, allow_multiple=allow_multiple, lang=DEFAULT_LANG,
    )
    return web.HTTPFound(f"/surveys/{name}")


async def survey_clone_post(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    survey = await bot.db.get_survey(name)
    if not survey:
        raise web.HTTPNotFound(text="Опрос не найден")

    base = f"{name}-copy"
    new_name = base
    n = 2
    while await bot.db.get_survey(new_name):
        new_name = f"{base}{n}"
        n += 1

    await bot.db.create_survey(
        name=new_name, title=f"{survey.get('title') or name} (копия)",
        intro=survey.get("intro") or "", outro=survey.get("outro") or "",
        questions=survey["questions"], anonymous=survey["anonymous"],
        allow_multiple=survey["allow_multiple"], creator_id=None,
        lang=survey.get("lang") or DEFAULT_LANG,
    )
    return web.HTTPFound(f"/surveys/{new_name}?cloned=1")


async def survey_detail(request: web.Request):
    bot = request.app["bot"]
    name = request.match_info["name"]
    survey = await bot.db.get_survey(name)
    if not survey:
        raise web.HTTPNotFound(text="Опрос не найден")
    responses = await bot.db.get_responses(survey["id"])
    resp_blocks = []
    for i, r in enumerate(responses, start=1):
        who = f"Аноним #{i}" if survey["anonymous"] else str(r["user_id"])
        answers = r["answers"]
        qa_rows = "".join(
            f"<tr><td class='muted' style='width:40%;vertical-align:top'>{html.escape(a.get('question', ''))}</td>"
            f"<td style='white-space:pre-wrap'>{html.escape(str(a.get('answer', '')) or '—')}</td></tr>"
            for a in (
                answers.get(str(idx)) for idx in range(len(survey["questions"]))
            )
            if a is not None
        )
        resp_blocks.append(f"""
        <details class="q-block">
          <summary style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:10px;">
            <span>{html.escape(who)}</span>
            <span class="muted">{html.escape(r['submitted_at'])}</span>
          </summary>
          <table style="margin-top:12px">{qa_rows}</table>
        </details>
        """)
    responses_html = "".join(resp_blocks) or "<p class='muted'>Ответов пока нет.</p>"
    publish_cmd = f"/survey publish name:{name}"
    cloned_notice = ""
    if request.query.get("cloned"):
        cloned_notice = f"""
        <div class="card" style="border-color:#5865f2;background:#2b2d3a;">
          <p style="margin:0">✅ Копия опроса создана под именем <code>{html.escape(name)}</code>.
          У неё <b>свой</b> список ответов, никак не связанный с оригиналом.</p>
          <p class="muted" style="margin:8px 0 0">Важно: чтобы люди проходили именно копию, нужно
          опубликовать <b>новую</b> кнопку командой ниже — старая кнопка в Discord-канале
          по-прежнему ведёт на оригинальный опрос, и тем, кто уже проходил его, она снова
          скажет «вы уже участвовали». Это нормально: та кнопка и есть оригинал.</p>
        </div>"""
    body = f"""
    {cloned_notice}
    <div class="card">
      <div class="row" style="align-items:center;justify-content:space-between;">
        <h2 style="margin:0">{html.escape(title_str(survey))}
          <span class="pill">{len(responses)} прош{'ёл' if len(responses)==1 else 'ло'}</span>
          <span class="pill">{lang_badge(survey)}</span></h2>
        <div class="row" style="flex:none;gap:8px">
          <a class="btn secondary" href="/surveys/{html.escape(name)}/edit">Редактировать</a>
          <form method="post" action="/surveys/{html.escape(name)}/clone">
            <button class="secondary" type="submit">Клонировать</button>
          </form>
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
      <h3>Пригласить людей пройти опрос</h3>
      <p class="muted">Зайди в нужный Discord-канал и выполни там эту команду —
      бот опубликует карточку сразу на двух языках (🇷🇺 + 🇬🇧) с кнопкой
      "📋 Пройти опрос / Take survey". Каждый пройдёт опрос на своём личном
      языке (выбранном в Discord), независимо от языка карточки:</p>
      <code class="copyline"><span id="publish-cmd">{html.escape(publish_cmd)}</span>
        <button type="button" onclick="copyText('publish-cmd', this)">Копировать</button></code>
    </div>
    <div class="card">
      <h3>Кто прошёл опрос <span class="muted" style="font-weight:normal;font-size:13px">(нажмите на строку, чтобы посмотреть ответы)</span></h3>
      {responses_html}
    </div>
    <script>
    function copyText(elId, btn) {{
      const el = document.getElementById(elId);
      const text = el.textContent.trim();
      const onOk = () => {{
        const old = btn.textContent;
        btn.textContent = 'Скопировано!';
        setTimeout(() => {{ btn.textContent = old; }}, 1500);
      }};
      const fallback = () => {{
        // navigator.clipboard недоступен без HTTPS — копируем через выделение текста
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try {{
          document.execCommand('copy');
          onOk();
        }} catch (err) {{
          alert('Не удалось скопировать автоматически. Скопируйте вручную: ' + text);
        }}
        document.body.removeChild(ta);
      }};
      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(text).then(onOk).catch(fallback);
      }} else {{
        fallback();
      }}
    }}
    </script>
    """
    return web.Response(text=page(title_str(survey) or name, body), content_type="text/html")


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
    app.router.add_get("/surveys/{name}/edit", survey_edit_get)
    app.router.add_post("/surveys/{name}/edit", survey_edit_post)
    app.router.add_post("/surveys/{name}/clone", survey_clone_post)
    app.router.add_get("/surveys/{name}/export.csv", survey_export_csv)
    app.router.add_post("/surveys/{name}/delete", survey_delete)

    from web.events_panel import add_routes as add_event_routes
    add_event_routes(app)

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
