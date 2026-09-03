import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",")]
COMMUNITY_CHAT_URL = os.getenv("COMMUNITY_CHAT_URL", "https://t.me/chief_posting_bot")
DB_PATH = "chiefbot.db"

# Точка
TOCHKA_API_TOKEN = os.getenv("TOCHKA_API_TOKEN", "")
TOCHKA_CUSTOMER_CODE = "301511177"
TOCHKA_MERCHANT_ID = "200000000041437"
TOCHKA_CLIENT_ID = os.getenv("TOCHKA_CLIENT_ID", "062962f583939ce97ef1820546e49aaf")
# CheapAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "pFTc8657vsPNGBJnLC9vIJNcdclKJbU7N3FTSptzDQHDzA3o")
BASE_URL = "https://cheapai.io/v1"
