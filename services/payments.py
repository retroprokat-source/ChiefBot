# services/payments.py
import json
import uuid
import base64
import requests
from datetime import datetime
import config
import database as db

CERT_FILE = "russian_certs.pem"

def create_payment_link(user_id: str, amount: str, purpose: str) -> str:
    """
    Создаёт платёжную ссылку в Точке.
    Возвращает paymentLinkId или None при ошибке.
    """
    url = "https://enter.tochka.com/uapi/acquiring/v1.0/payments"
    payment_link_id = str(uuid.uuid4())

    payload = {
        "Data": {
            "customerCode": config.TOCHKA_CUSTOMER_CODE,
            "merchantId": config.TOCHKA_MERCHANT_ID,
            "amount": amount,
            "purpose": purpose,
            "redirectUrl": "https://t.me/chief_posting_bot",
            "failRedirectUrl": "https://t.me/chief_posting_bot",
            "webhookUrl": "https://chiefbot.onrender.com/webhook/tochka",
            "paymentMode": ["sbp", "card"],
            "saveCard": False,
            "preAuthorization": False,
            "ttl": 10080,
            "paymentLinkId": payment_link_id
        }
    }

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {config.TOCHKA_API_TOKEN}'
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30, verify=CERT_FILE)
        if response.status_code == 200:
            data = response.json()
            payment_url = data.get("Data", {}).get("paymentUrl") or data.get("Data", {}).get("paymentLink")
            if payment_url:
                # Сохраняем в БД
                db.add_payment(user_id, "", float(amount), purpose, payment_link_id)
                return payment_url
        return None
    except Exception as e:
        print(f"❌ Ошибка создания платежа: {e}")
        return None


def setup_webhook():
    """Регистрирует вебхук в Точке."""
    url = f"https://enter.tochka.com/uapi/webhook/v1.0/{config.TOCHKA_CLIENT_ID}"
    payload = {
        "webhooksList": ["acquiringInternetPayment"],
        "url": "https://chiefbot.onrender.com/webhook/tochka"
    }
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {config.TOCHKA_API_TOKEN}'
    }

    try:
        response = requests.put(url, json=payload, headers=headers, timeout=15, verify=CERT_FILE)
        if response.status_code == 200:
            print("✅ Вебхук Точки зарегистрирован")
            return True
        else:
            print(f"❌ Ошибка регистрации вебхука: {response.status_code} {response.text}")
            return False
    except Exception as e:
        print(f"❌ Ошибка подключения к Точке: {e}")
        return False


def process_webhook(raw_body: str) -> dict:
    """Декодирует JWT-вебхук от Точки."""
    try:
        parts = raw_body.split('.')
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
        decoded = base64.b64decode(payload_b64).decode('utf-8')
        return json.loads(decoded)
    except Exception as e:
        print(f"❌ Ошибка декодирования вебхука: {e}")
        return {}
