# database.py
import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH

# ---------------------------- Инициализация БД ----------------------------

def init_db():
    """Создаёт все таблицы, если их ещё нет."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            plan TEXT DEFAULT 'free',
            plan_expires DATE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            owner_id TEXT,
            title TEXT,
            username TEXT,
            verified BOOLEAN DEFAULT 0,
            connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            content TEXT,
            media_type TEXT,
            media_file_id TEXT,
            scheduled_at TIMESTAMP,
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            user_id TEXT,
            expires_at TIMESTAMP,
            status TEXT DEFAULT 'active'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            channel_id TEXT,
            amount REAL,
            purpose TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promocodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL,
            duration_days INTEGER NOT NULL,
            uses_left INTEGER DEFAULT 1,
            created_by TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            date DATE,
            ai_generations INTEGER DEFAULT 0,
            posts_created INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

# ---------------------------- Пользователи ----------------------------

def add_user(user_id: str, username: str = None):
    """Добавляет пользователя, если его ещё нет."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)
    """, (user_id, username))
    conn.commit()
    conn.close()

def get_user(user_id: str):
    """Возвращает данные пользователя или None."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "username": row[1],
            "created_at": row[2],
            "plan": row[3],
            "plan_expires": row[4]
        }
    return None

def update_user_plan(user_id: str, plan: str, expires_date: str = None):
    """Обновляет тариф пользователя (например, при активации промокода)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        UPDATE users SET plan = ?, plan_expires = ? WHERE id = ?
    """, (plan, expires_date, user_id))
    conn.commit()
    conn.close()

# ---------------------------- Каналы ----------------------------

def add_channel(chat_id: str, owner_id: str, title: str, username: str = None):
    """Добавляет канал в базу (неверифицированный)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO channels (id, owner_id, title, username, verified)
        VALUES (?, ?, ?, ?, 0)
    """, (chat_id, owner_id, title, username))
    conn.commit()
    conn.close()

def verify_channel(chat_id: str):
    """Помечает канал как верифицированный."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE channels SET verified = 1 WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()

def get_user_channels(owner_id: str):
    """Возвращает список каналов пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM channels WHERE owner_id = ?", (owner_id,))
    rows = cur.fetchall()
    conn.close()
    channels = []
    for row in rows:
        channels.append({
            "id": row[0],
            "owner_id": row[1],
            "title": row[2],
            "username": row[3],
            "verified": row[4],
            "connected_at": row[5]
        })
    return channels

# ---------------------------- Посты ----------------------------

def add_post(channel_id: str, content: str, media_type: str = None,
             media_file_id: str = None, status: str = "posted"):
    """Сохраняет пост в базу."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO posts (channel_id, content, media_type, media_file_id, status)
        VALUES (?, ?, ?, ?, ?)
    """, (channel_id, content, media_type, media_file_id, status))
    conn.commit()
    conn.close()

# ---------------------------- Промокоды ----------------------------

def add_promocode(code: str, plan: str, duration_days: int, uses_left: int, created_by: str):
    """Создаёт новый промокод."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO promocodes (code, plan, duration_days, uses_left, created_by)
        VALUES (?, ?, ?, ?, ?)
    """, (code, plan, duration_days, uses_left, created_by))
    conn.commit()
    conn.close()

def get_promocode(code: str):
    """Возвращает данные промокода или None."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM promocodes WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "code": row[1],
            "plan": row[2],
            "duration_days": row[3],
            "uses_left": row[4],
            "created_by": row[5]
        }
    return None

def activate_promocode(code: str, user_id: str):
    """
    Активирует промокод для пользователя.
    Возвращает (успех, сообщение).
    """
    promo = get_promocode(code)
    if not promo:
        return False, "Промокод не найден."

    if promo["uses_left"] <= 0:
        return False, "Промокод уже использован."

    # Вычисляем дату окончания подписки
    expires = (datetime.now() + timedelta(days=promo["duration_days"])).strftime("%Y-%m-%d")

    # Обновляем пользователя
    update_user_plan(user_id, promo["plan"], expires)

    # Уменьшаем количество использований
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()

    return True, f"Промокод активирован! Ваш тариф: {promo['plan']} до {expires}."

# ---------------------------- Лимиты ----------------------------

def check_limits(user_id: str):
    """
    Проверяет лимиты пользователя.
    Возвращает словарь с текущими лимитами и количеством использованных.
    """
    user = get_user(user_id)
    if not user:
        return {"allowed_channels": 0, "current_channels": 0, "allowed_posts": 0, "current_posts": 0}

    # Лимиты по тарифу
    limits = {
        "free": {"channels": 1, "posts": 10},
        "pro": {"channels": 5, "posts": 100},
        "premium": {"channels": 20, "posts": 999999}  # практически безлимит
    }
    plan = user.get("plan", "free")
    current_channels = len(get_user_channels(user_id))
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM posts WHERE channel_id IN (SELECT id FROM channels WHERE owner_id = ?)", (user_id,))
    current_posts = cur.fetchone()[0]
    conn.close()

    return {
        "allowed_channels": limits[plan]["channels"],
        "current_channels": current_channels,
        "allowed_posts": limits[plan]["posts"],
        "current_posts": current_posts
    }
