import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",")]
COMMUNITY_CHAT_URL = os.getenv("COMMUNITY_CHAT_URL", "https://t.me/chief_posting_bot")
DB_PATH = "chiefbot.db"

# Точка
TOCHKA_API_TOKEN = os.getenv("TOCHKA_API_TOKEN", "")
TOCHKA_CUSTOMER_CODE = "301511177"
TOCHKA_MERCHANT_ID = "200000000041437"
TOCHKA_CLIENT_ID = "5e3f88c12690b3086faf7fa0daf46efa"
