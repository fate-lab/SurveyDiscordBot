"""
Тексты, которые видит человек, проходящий опрос. Выбираются по полю
survey["lang"] ('ru' или 'en'). Админские команды/сообщения (создание,
ошибки прав и т.п.) намеренно остаются на русском — это интерфейс для вас,
а не для респондентов.
"""

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en")

STRINGS = {
    "ru": {
        "other_option_label": "Другое",
        "other_modal_title": "Другое",
        "other_modal_label": "Уточни свой вариант",
        "other_prefix": "Другое: ",
        "done_button": "✅ Готово",
        "answer_button": "✍️ Ответить",
        "answer_modal_title": "Твой ответ",
        "answer_received": "Ответ принят ✅",
        "select_single_placeholder": "Выбери вариант...",
        "select_multi_placeholder": "Выбери один или несколько вариантов...",
        "multi_hint": " _(можно выбрать несколько, затем нажми «Готово»)_",
        "multi_need_one": "Выбери хотя бы один вариант перед тем, как нажать «Готово».",
        "yes_label_default": "Да",
        "no_label_default": "Нет",
        "yes_modal_title": "Опиши коротко",
        "yes_modal_label": "Что случилось?",
        "yes_prefix": "Да: ",
        "no_answer": "Нет",
        "timeout_answer": "(нет ответа — время вышло)",
        "already_responded": "Ты уже проходил(а) этот опрос. Спасибо! 🙏",
        "survey_gone": "Этот опрос больше не существует.",
        "opened_in_channel": "✅ Опрос открыт в {channel}",
        "channel_prefix": "опрос-",
        "default_outro": "Спасибо за ответы! 🙏",
        "channel_autodelete": "_Этот канал будет автоматически удалён через 20 секунд..._",
        "run_error": "⚠️ Произошла ошибка при прохождении опроса. Обратись к администратору.",
        "bot_restarting": "Бот перезагружается, попробуй чуть позже.",
        "take_survey_button": "📋 Пройти опрос",
    },
    "en": {
        "other_option_label": "Other",
        "other_modal_title": "Other",
        "other_modal_label": "Please specify",
        "other_prefix": "Other: ",
        "done_button": "✅ Done",
        "answer_button": "✍️ Answer",
        "answer_modal_title": "Your answer",
        "answer_received": "Answer received ✅",
        "select_single_placeholder": "Choose an option...",
        "select_multi_placeholder": "Choose one or more options...",
        "multi_hint": " _(you can pick several, then click “Done”)_",
        "multi_need_one": "Pick at least one option before clicking “Done”.",
        "yes_label_default": "Yes",
        "no_label_default": "No",
        "yes_modal_title": "Tell us briefly",
        "yes_modal_label": "What happened?",
        "yes_prefix": "Yes: ",
        "no_answer": "No",
        "timeout_answer": "(no answer — time ran out)",
        "already_responded": "You've already completed this survey. Thanks! 🙏",
        "survey_gone": "This survey no longer exists.",
        "opened_in_channel": "✅ Survey opened in {channel}",
        "channel_prefix": "survey-",
        "default_outro": "Thanks for your answers! 🙏",
        "channel_autodelete": "_This channel will be automatically deleted in 20 seconds..._",
        "run_error": "⚠️ Something went wrong while running the survey. Please contact an admin.",
        "bot_restarting": "The bot is restarting, please try again shortly.",
        "take_survey_button": "📋 Take survey",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    text = STRINGS[lang].get(key, STRINGS[DEFAULT_LANG].get(key, key))
    return text.format(**kwargs) if kwargs else text


def other_lang(lang: str) -> str:
    return "en" if lang == "ru" else "ru"


def loc(value, lang: str):
    """Достаёт текст/список на нужном языке из поля опроса.

    Поддерживает новый двуязычный формат {"ru": ..., "en": ...} (используется
    веб-панелью при создании/редактировании опроса) и старый формат — просто
    строка/список — для опросов, созданных до появления двух языков. Если для
    выбранного языка ничего не заполнено, подставляет то, что есть на втором
    языке, чтобы опрос не ломался, пока не заполнили перевод.
    """
    if isinstance(value, dict):
        val = value.get(lang)
        if val not in (None, "", []):
            return val
        return value.get(other_lang(lang)) or ("" if not isinstance(value.get(other_lang(lang)), list) else [])
    return value


def has_any_lang(value) -> bool:
    if isinstance(value, dict):
        return bool(value.get("ru")) or bool(value.get("en"))
    return bool(value)
