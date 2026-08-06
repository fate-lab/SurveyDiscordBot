import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("DISCORD_TOKEN", "")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "surveys.db")
CATEGORY_NAME = os.getenv("SURVEY_CATEGORY", "📋 Опросы")
EVENT_CATEGORY_NAME = os.getenv("EVENT_CATEGORY", "🎫 События")

# --- Веб-панель ---
# WEB_PORT должен совпадать с портом, который выдал хостинг (Wispbyte и т.п.),
# т.к. на бесплатном тарифе обычно доступен только ОДИН внешний порт.
WEB_ENABLED = os.getenv("WEB_ENABLED", "1") == "1"
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "14444") or 14444)
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")
