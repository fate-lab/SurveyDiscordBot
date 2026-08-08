import json
import datetime
import aiosqlite

from i18n import DEFAULT_LANG


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def init(self):
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS surveys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                title TEXT,
                intro TEXT,
                outro TEXT,
                questions TEXT NOT NULL,
                anonymous INTEGER NOT NULL DEFAULT 1,
                allow_multiple INTEGER NOT NULL DEFAULT 0,
                creator_id INTEGER,
                created_at TEXT,
                lang TEXT NOT NULL DEFAULT 'ru'
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                answers TEXT NOT NULL,
                submitted_at TEXT,
                FOREIGN KEY(survey_id) REFERENCES surveys(id)
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                capacity INTEGER NOT NULL,
                event_dt TEXT NOT NULL,
                role_id INTEGER,
                channel_id INTEGER,
                announce_channel_id INTEGER,
                announce_message_id INTEGER,
                creator_id INTEGER,
                created_at TEXT,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'joined',
                joined_at TEXT,
                FOREIGN KEY(event_id) REFERENCES events(id),
                UNIQUE(event_id, user_id)
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_language (
                user_id INTEGER PRIMARY KEY,
                lang TEXT NOT NULL DEFAULT 'en'
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lang_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message_id INTEGER
            )
            """
        )
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self):
        """Добавляет колонки, которых не было в более старых версиях базы,
        не трогая уже сохранённые опросы/ответы."""
        cur = await self._conn.execute("PRAGMA table_info(surveys)")
        cols = {row[1] for row in await cur.fetchall()}
        if "lang" not in cols:
            await self._conn.execute(
                f"ALTER TABLE surveys ADD COLUMN lang TEXT NOT NULL DEFAULT '{DEFAULT_LANG}'"
            )

    @staticmethod
    def _pack_text_field(value):
        """title/intro/outro теперь могут быть либо обычной строкой (старые
        одноязычные опросы), либо словарём {"ru": ..., "en": ...} (новые
        двуязычные опросы из веб-панели) — сохраняем словарь как JSON."""
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _unpack_text_field(value):
        if not value:
            return value
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        return parsed if isinstance(parsed, dict) else value

    async def create_survey(self, name, title, intro, outro, questions,
                             anonymous, allow_multiple, creator_id, lang=DEFAULT_LANG):
        await self._conn.execute(
            """INSERT INTO surveys
               (name, title, intro, outro, questions, anonymous, allow_multiple,
                creator_id, created_at, lang)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, self._pack_text_field(title), self._pack_text_field(intro),
                self._pack_text_field(outro),
                json.dumps(questions, ensure_ascii=False),
                int(anonymous), int(allow_multiple), creator_id,
                datetime.datetime.utcnow().isoformat(), lang,
            ),
        )
        await self._conn.commit()

    async def update_survey(self, name, title, intro, outro, questions,
                             anonymous, allow_multiple, lang=DEFAULT_LANG):
        await self._conn.execute(
            """UPDATE surveys SET title=?, intro=?, outro=?, questions=?,
               anonymous=?, allow_multiple=?, lang=? WHERE name=?""",
            (
                self._pack_text_field(title), self._pack_text_field(intro),
                self._pack_text_field(outro), json.dumps(questions, ensure_ascii=False),
                int(anonymous), int(allow_multiple), lang, name,
            ),
        )
        await self._conn.commit()

    async def get_survey(self, name):
        cur = await self._conn.execute("SELECT * FROM surveys WHERE name = ?", (name,))
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))
        data["questions"] = json.loads(data["questions"])
        data["anonymous"] = bool(data["anonymous"])
        data["allow_multiple"] = bool(data["allow_multiple"])
        data["lang"] = data.get("lang") or DEFAULT_LANG
        data["title"] = self._unpack_text_field(data.get("title"))
        data["intro"] = self._unpack_text_field(data.get("intro"))
        data["outro"] = self._unpack_text_field(data.get("outro"))
        return data

    async def list_surveys(self):
        cur = await self._conn.execute(
            "SELECT name, title, questions, lang FROM surveys ORDER BY id"
        )
        rows = await cur.fetchall()
        result = []
        for name, title, questions, lang in rows:
            result.append({
                "name": name,
                "title": self._unpack_text_field(title),
                "question_count": len(json.loads(questions)),
                "lang": lang or DEFAULT_LANG,
            })
        return result

    async def delete_survey(self, name):
        survey = await self.get_survey(name)
        if not survey:
            return False
        await self._conn.execute("DELETE FROM responses WHERE survey_id = ?", (survey["id"],))
        await self._conn.execute("DELETE FROM surveys WHERE id = ?", (survey["id"],))
        await self._conn.commit()
        return True

    async def has_responded(self, survey_id, user_id):
        cur = await self._conn.execute(
            "SELECT 1 FROM responses WHERE survey_id = ? AND user_id = ? LIMIT 1",
            (survey_id, user_id),
        )
        return (await cur.fetchone()) is not None

    async def save_response(self, survey_id, user_id, answers):
        await self._conn.execute(
            "INSERT INTO responses (survey_id, user_id, answers, submitted_at) VALUES (?, ?, ?, ?)",
            (survey_id, user_id, json.dumps(answers, ensure_ascii=False),
             datetime.datetime.utcnow().isoformat()),
        )
        await self._conn.commit()

    async def get_responses(self, survey_id):
        cur = await self._conn.execute(
            "SELECT user_id, answers, submitted_at FROM responses WHERE survey_id = ? ORDER BY id",
            (survey_id,),
        )
        rows = await cur.fetchall()
        return [
            {"user_id": r[0], "answers": json.loads(r[1]), "submitted_at": r[2]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # События (events)
    # ------------------------------------------------------------------

    async def create_event(self, guild_id, title, description, capacity,
                            event_dt, role_id, creator_id):
        cur = await self._conn.execute(
            """INSERT INTO events
               (guild_id, title, description, capacity, event_dt, role_id,
                creator_id, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                guild_id, title, description, capacity, event_dt, role_id,
                creator_id, datetime.datetime.utcnow().isoformat(),
            ),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def set_event_message(self, event_id, channel_id, announce_channel_id, announce_message_id):
        await self._conn.execute(
            "UPDATE events SET channel_id=?, announce_channel_id=?, announce_message_id=? WHERE id=?",
            (channel_id, announce_channel_id, announce_message_id, event_id),
        )
        await self._conn.commit()

    async def update_event(self, event_id, title, description, capacity, event_dt, role_id=None):
        if role_id is not None:
            await self._conn.execute(
                """UPDATE events SET title=?, description=?, capacity=?, event_dt=?, role_id=?
                   WHERE id=?""",
                (title, description, capacity, event_dt, role_id, event_id),
            )
        else:
            await self._conn.execute(
                """UPDATE events SET title=?, description=?, capacity=?, event_dt=?
                   WHERE id=?""",
                (title, description, capacity, event_dt, event_id),
            )
        await self._conn.commit()

    async def set_event_status(self, event_id, status):
        await self._conn.execute("UPDATE events SET status=? WHERE id=?", (status, event_id))
        await self._conn.commit()

    async def get_event(self, event_id):
        cur = await self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get_event_by_message(self, message_id):
        cur = await self._conn.execute("SELECT * FROM events WHERE announce_message_id = ?", (message_id,))
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def list_events(self, guild_id=None, status=None):
        query = "SELECT * FROM events"
        conds, params = [], []
        if guild_id is not None:
            conds.append("guild_id = ?")
            params.append(guild_id)
        if status is not None:
            conds.append("status = ?")
            params.append(status)
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY id DESC"
        cur = await self._conn.execute(query, params)
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    async def delete_event(self, event_id):
        await self._conn.execute("DELETE FROM event_participants WHERE event_id = ?", (event_id,))
        await self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await self._conn.commit()

    async def get_participant(self, event_id, user_id):
        cur = await self._conn.execute(
            "SELECT * FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def add_participant(self, event_id, user_id, status="joined"):
        await self._conn.execute(
            """INSERT INTO event_participants (event_id, user_id, status, joined_at)
               VALUES (?, ?, ?, ?)""",
            (event_id, user_id, status, datetime.datetime.utcnow().isoformat()),
        )
        await self._conn.commit()

    async def set_participant_status(self, event_id, user_id, status):
        await self._conn.execute(
            "UPDATE event_participants SET status=? WHERE event_id=? AND user_id=?",
            (status, event_id, user_id),
        )
        await self._conn.commit()

    async def remove_participant(self, event_id, user_id):
        await self._conn.execute(
            "DELETE FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        await self._conn.commit()

    async def list_participants(self, event_id, status=None):
        if status:
            cur = await self._conn.execute(
                "SELECT * FROM event_participants WHERE event_id = ? AND status = ? ORDER BY id",
                (event_id, status),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM event_participants WHERE event_id = ? ORDER BY id",
                (event_id,),
            )
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    async def count_participants(self, event_id, status="joined"):
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM event_participants WHERE event_id = ? AND status = ?",
            (event_id, status),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Язык пользователя
    # ------------------------------------------------------------------

    async def get_user_lang(self, user_id) -> str:
        cur = await self._conn.execute("SELECT lang FROM user_language WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else "en"

    async def get_user_lang_raw(self, user_id):
        """Как get_user_lang, но возвращает None, если пользователь ещё ни разу
        не выбирал язык (в отличие от get_user_lang, который в этом случае
        молча подставляет 'en'). Нужно, чтобы понять, надо ли спросить язык
        перед началом опроса."""
        cur = await self._conn.execute("SELECT lang FROM user_language WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_user_lang(self, user_id, lang: str):
        await self._conn.execute(
            """INSERT INTO user_language (user_id, lang) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang""",
            (user_id, lang),
        )
        await self._conn.commit()

    async def count_users_by_lang(self):
        cur = await self._conn.execute("SELECT lang, COUNT(*) FROM user_language GROUP BY lang")
        rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}

    async def set_lang_channel(self, guild_id, channel_id, message_id):
        await self._conn.execute(
            """INSERT INTO lang_channels (guild_id, channel_id, message_id) VALUES (?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id,
                                                    message_id = excluded.message_id""",
            (guild_id, channel_id, message_id),
        )
        await self._conn.commit()

    async def get_lang_channel(self, guild_id):
        cur = await self._conn.execute(
            "SELECT channel_id, message_id FROM lang_channels WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        return {"channel_id": row[0], "message_id": row[1]} if row else None

    async def list_lang_channels(self):
        cur = await self._conn.execute("SELECT guild_id, channel_id, message_id FROM lang_channels")
        rows = await cur.fetchall()
        return [{"guild_id": r[0], "channel_id": r[1], "message_id": r[2]} for r in rows]
