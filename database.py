from typing import Optional, List
import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                telegram_id INTEGER,
                config_issued INTEGER NOT NULL DEFAULT 0,
                client_ip TEXT,
                private_key TEXT,
                public_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def add_user(name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO users (name) VALUES (?)", (name,))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def get_user_by_name(name: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE name = ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_telegram_id(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def remove_user_by_name(name: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE name = ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        user = dict(row)
        await db.execute("DELETE FROM users WHERE name = ?", (name,))
        await db.commit()
        return user


async def list_users() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY created_at") as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def bind_telegram_id(name: str, telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET telegram_id = ? WHERE name = ?",
            (telegram_id, name),
        )
        await db.commit()


async def mark_issued(name: str, client_ip: str, private_key: str, public_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE users
               SET config_issued = 1, client_ip = ?, private_key = ?, public_key = ?
               WHERE name = ?""",
            (client_ip, private_key, public_key, name),
        )
        await db.commit()


async def reset_user_key(name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE users
               SET config_issued = 0, client_ip = NULL, private_key = NULL, public_key = NULL,
                   telegram_id = NULL
               WHERE name = ?""",
            (name,),
        )
        await db.commit()
        return cursor.rowcount > 0
