import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",")]
COMMUNITY_CHAT_URL = os.getenv("COMMUNITY_CHAT_URL", "https://t.me/chief_posting_bot")
DB_PATH = "chiefbot.db"
