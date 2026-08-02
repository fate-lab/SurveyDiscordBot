import json
import datetime
import aiosqlite


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
                created_at TEXT
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
        await self._conn.commit()

    async def create_survey(self, name, title, intro, outro, questions,
                             anonymous, allow_multiple, creator_id):
        await self._conn.execute(
            """INSERT INTO surveys
               (name, title, intro, outro, questions, anonymous, allow_multiple, creator_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, title, intro, outro,
                json.dumps(questions, ensure_ascii=False),
                int(anonymous), int(allow_multiple), creator_id,
                datetime.datetime.utcnow().isoformat(),
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
        return data

    async def list_surveys(self):
        cur = await self._conn.execute("SELECT name, title, questions FROM surveys ORDER BY id")
        rows = await cur.fetchall()
        result = []
        for name, title, questions in rows:
            result.append({
                "name": name,
                "title": title,
                "question_count": len(json.loads(questions)),
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
