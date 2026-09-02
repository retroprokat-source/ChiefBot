# services/subscriptions.py
from datetime import datetime, timedelta
import database as db
import services.payments as payments_service

SUBSCRIPTION_PRICE = "1.00"  # Тестовая цена 1 ₽
SUBSCRIPTION_DAYS = 30  # Длительность подписки в днях


def create_subscription_payment(user_id: str, channel_id: str) -> str:
    """
    Создаёт платёжную ссылку для подписки на канал.
    Возвращает URL оплаты или None.
    """
    purpose = f"Подписка на канал {channel_id} на {SUBSCRIPTION_DAYS} дней"
    return payments_service.create_payment_link(
        user_id=user_id,
        amount=SUBSCRIPTION_PRICE,
        purpose=purpose
    )


def activate_subscription(user_id: str, channel_id: str, days: int = SUBSCRIPTION_DAYS):
    """
    Активирует подписку пользователя на канал.
    """
    expires_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db.add_subscriber(channel_id, user_id, expires_at)
    return expires_at
