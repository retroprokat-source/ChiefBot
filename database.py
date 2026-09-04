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
            plan_expires DATE,
            timezone TEXT DEFAULT NULL,
            role TEXT DEFAULT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            owner_id TEXT,
            title TEXT,
            username TEXT,
            verified BOOLEAN DEFAULT 0,
            connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            subscription_price TEXT DEFAULT NULL,
            payment_link TEXT DEFAULT NULL,
            payment_instructions TEXT DEFAULT NULL
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
            status TEXT DEFAULT 'active',
            notified_3d BOOLEAN DEFAULT 0,
            notified_1d BOOLEAN DEFAULT 0
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
            payment_link_id TEXT,
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

def migrate():
    """Добавляет недостающие поля."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Добавляем timezone в users
    cur.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cur.fetchall()]
    if "timezone" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT NULL")

    # Добавляем notified_3d и notified_1d в subscribers
    cur.execute("PRAGMA table_info(subscribers)")
    columns = [col[1] for col in cur.fetchall()]
    if "notified_3d" not in columns:
        cur.execute("ALTER TABLE subscribers ADD COLUMN notified_3d BOOLEAN DEFAULT 0")
    if "notified_1d" not in columns:
        cur.execute("ALTER TABLE subscribers ADD COLUMN notified_1d BOOLEAN DEFAULT 0")

    # Добавляем поля настроек подписки в channels
    cur.execute("PRAGMA table_info(channels)")
    columns = [col[1] for col in cur.fetchall()]
    if "subscription_price" not in columns:
        cur.execute("ALTER TABLE channels ADD COLUMN subscription_price TEXT DEFAULT NULL")
    if "payment_link" not in columns:
        cur.execute("ALTER TABLE channels ADD COLUMN payment_link TEXT DEFAULT NULL")
    if "payment_instructions" not in columns:
        cur.execute("ALTER TABLE channels ADD COLUMN payment_instructions TEXT DEFAULT NULL")

    # Добавляем role в users
    cur.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cur.fetchall()]
    if "role" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT NULL")

    conn.commit()
    conn.close()

# ---------------------------- Пользователи ----------------------------

def add_user(user_id: str, username: str = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def get_user(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "username": row[1], "created_at": row[2], "plan": row[3], "plan_expires": row[4]}
    return None

def update_user_plan(user_id: str, plan: str, expires_date: str = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET plan = ?, plan_expires = ? WHERE id = ?", (plan, expires_date, user_id))
    conn.commit()
    conn.close()

# ---------------------------- Часовые пояса ----------------------------

def set_user_timezone(user_id: str, timezone: str):
    """Сохраняет часовой пояс пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET timezone = ? WHERE id = ?", (timezone, user_id))
    conn.commit()
    conn.close()

def get_user_timezone(user_id: str):
    """Возвращает часовой пояс пользователя или None."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT timezone FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None

# ---------------------------- Каналы ----------------------------

def add_channel(chat_id: str, owner_id: str, title: str, username: str = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO channels (id, owner_id, title, username, verified) VALUES (?, ?, ?, ?, 0)", (chat_id, owner_id, title, username))
    conn.commit()
    conn.close()

def verify_channel(chat_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE channels SET verified = 1 WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()

def get_user_channels(owner_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM channels WHERE owner_id = ?", (owner_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "owner_id": r[1], "title": r[2], "username": r[3], "verified": r[4], "connected_at": r[5]} for r in rows]

def get_channel_by_id(channel_id: str):
    """Возвращает канал по ID."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "owner_id": row[1],
            "title": row[2],
            "username": row[3],
            "verified": row[4],
            "connected_at": row[5]
        }
    return None

# ---------------------------- Посты ----------------------------

def add_post(channel_id: str, content: str, media_type: str = None, media_file_id: str = None, status: str = "posted"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO posts (channel_id, content, media_type, media_file_id, status) VALUES (?, ?, ?, ?, ?)", (channel_id, content, media_type, media_file_id, status))
    conn.commit()
    conn.close()

def add_scheduled_post(channel_id: str, content: str, media_type: str, media_file_id: str, scheduled_at: str):
    """Сохраняет отложенный пост."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO posts (channel_id, content, media_type, media_file_id, scheduled_at, status)
        VALUES (?, ?, ?, ?, ?, 'scheduled')
    """, (channel_id, content, media_type, media_file_id, scheduled_at))
    conn.commit()
    conn.close()

# ---------------------------- Платежи ----------------------------

def add_payment(user_id: str, channel_id: str, amount: float, purpose: str, payment_link_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO payments (user_id, channel_id, amount, purpose, payment_link_id, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)", (user_id, channel_id, amount, purpose, payment_link_id))
    conn.commit()
    conn.close()

def update_payment_status(payment_link_id: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE payments SET status = ? WHERE payment_link_id = ?", (status, payment_link_id))
    conn.commit()
    conn.close()

def get_payment_by_link_id(payment_link_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM payments WHERE payment_link_id = ?", (payment_link_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "user_id": row[1], "channel_id": row[2], "amount": row[3], "purpose": row[4], "status": row[5], "payment_link_id": row[6], "created_at": row[7]}
    return None

# ---------------------------- Подписчики ----------------------------

def add_subscriber(channel_id: str, user_id: str, expires_at: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO subscribers (channel_id, user_id, expires_at, status) VALUES (?, ?, ?, 'active')", (channel_id, user_id, expires_at))
    conn.commit()
    conn.close()

def get_active_subscribers(channel_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, expires_at FROM subscribers WHERE channel_id = ? AND status = 'active' AND expires_at > datetime('now')", (channel_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"user_id": r[0], "expires_at": r[1]} for r in rows]

def get_expired_subscribers():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT channel_id, user_id FROM subscribers WHERE status = 'active' AND expires_at <= datetime('now')")
    rows = cur.fetchall()
    conn.close()
    return [{"channel_id": r[0], "user_id": r[1]} for r in rows]

def mark_subscriber_expired(channel_id: str, user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE subscribers SET status = 'expired' WHERE channel_id = ? AND user_id = ?", (channel_id, user_id))
    conn.commit()
    conn.close()

def get_user_subscription(user_id: str, channel_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT expires_at FROM subscribers WHERE user_id = ? AND channel_id = ? AND status = 'active' AND expires_at > datetime('now')", (user_id, channel_id))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

# ---------------------------- Промокоды ----------------------------

def add_promocode(code: str, plan: str, duration_days: int, uses_left: int, created_by: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO promocodes (code, plan, duration_days, uses_left, created_by) VALUES (?, ?, ?, ?, ?)", (code, plan, duration_days, uses_left, created_by))
    conn.commit()
    conn.close()

def get_promocode(code: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM promocodes WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "code": row[1], "plan": row[2], "duration_days": row[3], "uses_left": row[4], "created_by": row[5]}
    return None

def activate_promocode(code: str, user_id: str):
    promo = get_promocode(code)
    if not promo:
        return False, "Промокод не найден."
    if promo["uses_left"] <= 0:
        return False, "Промокод уже использован."
    expires = (datetime.now() + timedelta(days=promo["duration_days"])).strftime("%Y-%m-%d")
    update_user_plan(user_id, promo["plan"], expires)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return True, f"Промокод активирован! Ваш тариф: {promo['plan']} до {expires}."

# ---------------------------- Лимиты ----------------------------

def check_limits(user_id: str):
    user = get_user(user_id)
    if not user:
        add_user(user_id)
        user = get_user(user_id)

    limits = {
        "free": {"channels": 1, "posts": 10},
        "pro": {"channels": 5, "posts": 100},
        "premium": {"channels": 20, "posts": 999999}
    }

    plan = user.get("plan", "free")
    if plan not in limits:
        plan = "free"

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

# ---------------------------- Напоминания об истечении подписки ----------------------------

def get_subscriptions_expiring_in_days(days: int):
    """
    Возвращает подписки, которые истекают ровно через указанное количество дней.
    days: 3 или 1
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, channel_id, user_id, expires_at, notified_3d, notified_1d
        FROM subscribers
        WHERE status = 'active'
        AND date(expires_at) = date(datetime('now', ?))
    """, (f"+{days} days",))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "channel_id": r[1],
            "user_id": r[2],
            "expires_at": r[3],
            "notified_3d": r[4],
            "notified_1d": r[5]
        }
        for r in rows
    ]


def mark_notification_sent(subscription_id: int, days: int):
    """
    Помечает, что уведомление за days дней отправлено.
    days: 3 или 1
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if days == 3:
        cur.execute("UPDATE subscribers SET notified_3d = 1 WHERE id = ?", (subscription_id,))
    elif days == 1:
        cur.execute("UPDATE subscribers SET notified_1d = 1 WHERE id = ?", (subscription_id,))
    conn.commit()
    conn.close()

# ---------------------------- ИИ-генерации ----------------------------

def get_ai_generations_today(user_id: str) -> int:
    """Возвращает количество ИИ-генераций за сегодня."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(ai_generations, 0) FROM daily_usage
        WHERE user_id = ? AND date = date('now')
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def increment_ai_generations(user_id: str):
    """Увеличивает счётчик ИИ-генераций за сегодня."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO daily_usage (user_id, date, ai_generations)
        VALUES (?, date('now'), 1)
        ON CONFLICT DO NOTHING
    """, (user_id,))
    cur.execute("""
        UPDATE daily_usage SET ai_generations = ai_generations + 1
        WHERE user_id = ? AND date = date('now')
    """, (user_id,))
    conn.commit()
    conn.close()


# ---------------------------- Черновики ----------------------------

def add_draft(channel_id: str, content: str, media_type: str = None, media_file_id: str = None):
    """Сохраняет черновик поста."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO posts (channel_id, content, media_type, media_file_id, status)
        VALUES (?, ?, ?, ?, 'draft')
    """, (channel_id, content, media_type, media_file_id))
    conn.commit()
    conn.close()


def get_drafts(user_id: str):
    """Возвращает черновики пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.channel_id, p.content, p.media_type, p.media_file_id, p.created_at, c.title
        FROM posts p
        JOIN channels c ON p.channel_id = c.id
        WHERE c.owner_id = ? AND p.status = 'draft'
        ORDER BY p.created_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "channel_id": r[1],
            "content": r[2],
            "media_type": r[3],
            "media_file_id": r[4],
            "created_at": r[5],
            "channel_title": r[6]
        }
        for r in rows
    ]


def get_draft_by_id(draft_id: int):
    """Возвращает черновик по ID."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.channel_id, p.content, p.media_type, p.media_file_id, c.title
        FROM posts p
        JOIN channels c ON p.channel_id = c.id
        WHERE p.id = ? AND p.status = 'draft'
    """, (draft_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "channel_id": row[1],
            "content": row[2],
            "media_type": row[3],
            "media_file_id": row[4],
            "channel_title": row[5]
        }
    return None


def delete_draft(draft_id: int):
    """Удаляет черновик."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM posts WHERE id = ? AND status = 'draft'", (draft_id,))
    conn.commit()
    conn.close()


def update_draft_status(draft_id: int, status: str = "posted"):
    """Обновляет статус черновика."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE posts SET status = ? WHERE id = ?", (status, draft_id))
    conn.commit()
    conn.close()

# ---------------------------- Настройки подписки канала ----------------------------

def update_channel_subscription_settings(channel_id: str, price: str = None, payment_link: str = None, instructions: str = None):
    """Обновляет настройки подписки канала."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    if price is not None:
        cur.execute("UPDATE channels SET subscription_price = ? WHERE id = ?", (price, channel_id))
    if payment_link is not None:
        cur.execute("UPDATE channels SET payment_link = ? WHERE id = ?", (payment_link, channel_id))
    if instructions is not None:
        cur.execute("UPDATE channels SET payment_instructions = ? WHERE id = ?", (instructions, channel_id))
    
    conn.commit()
    conn.close()


def get_channel_subscription_settings(channel_id: str):
    """Возвращает настройки подписки канала."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT subscription_price, payment_link, payment_instructions
        FROM channels WHERE id = ?
    """, (channel_id,))
    row = cur.fetchone()
    conn.close()
    
    if row:
        return {
            "price": row[0],
            "payment_link": row[1],
            "instructions": row[2]
        }
    return {"price": None, "payment_link": None, "instructions": None}


def get_all_channels_for_subscribe():
    """Возвращает все верифицированные каналы с настройками подписки."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, subscription_price, payment_link, payment_instructions
        FROM channels
        WHERE verified = 1 AND subscription_price IS NOT NULL
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "title": r[1],
            "price": r[2],
            "payment_link": r[3],
            "instructions": r[4]
        }
        for r in rows
    ]

# ---------------------------- Заявки на подписку ----------------------------

def add_subscription_request(channel_id: str, user_id: str, username: str = None):
    """Добавляет заявку на подписку."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO subscribers (channel_id, user_id, status, created_at)
        VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)
    """, (channel_id, user_id))
    conn.commit()
    conn.close()


def get_pending_requests(channel_id: str):
    """Возвращает заявки на подписку со статусом pending."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.channel_id, s.user_id, s.created_at, c.title
        FROM subscribers s
        JOIN channels c ON s.channel_id = c.id
        WHERE s.channel_id = ? AND s.status = 'pending'
        ORDER BY s.created_at DESC
    """, (channel_id,))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "channel_id": r[1],
            "user_id": r[2],
            "created_at": r[3],
            "channel_title": r[4]
        }
        for r in rows
    ]


def approve_subscription_request(request_id: int):
    """Одобряет заявку и возвращает данные."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT channel_id, user_id FROM subscribers WHERE id = ? AND status = 'pending'", (request_id,))
    row = cur.fetchone()
    
    if not row:
        conn.close()
        return None
    
    channel_id = row[0]
    user_id = row[1]
    expires_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    
    cur.execute("UPDATE subscribers SET status = 'active', expires_at = ? WHERE id = ?", (expires_at, request_id))
    conn.commit()
    conn.close()
    
    return {"channel_id": channel_id, "user_id": user_id, "expires_at": expires_at}


def reject_subscription_request(request_id: int):
    """Отклоняет заявку."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE subscribers SET status = 'rejected' WHERE id = ?", (request_id,))
    conn.commit()
    conn.close()


def get_expired_subscribers_by_channel(channel_id: str):
    """Возвращает истёкших подписчиков канала."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, expires_at FROM subscribers
        WHERE channel_id = ? AND status = 'expired'
    """, (channel_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"user_id": r[0], "expires_at": r[1]} for r in rows]

# ---------------------------- Роли пользователей ----------------------------

def set_user_role(user_id: str, role: str):
    """Сохраняет роль пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    conn.close()


def get_user_role(user_id: str):
    """Возвращает роль пользователя или None."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None
