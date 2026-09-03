# services/subscriptions.py
from datetime import datetime, timedelta
import logging
import database as db
import services.payments as payments_service

SUBSCRIPTION_PRICE = "1.00"  # Тестовая цена по умолчанию
SUBSCRIPTION_DAYS = 30  # Длительность подписки в днях


def create_subscription_payment(user_id: str, channel_id: str, price: str = None) -> str:
    """
    Создаёт платёжную ссылку для подписки на канал.
    Сохраняет channel_id в платеже.
    Возвращает URL оплаты или None.
    """
    if price is None:
        price = SUBSCRIPTION_PRICE
    
    purpose = f"Подписка на канал {channel_id} на {SUBSCRIPTION_DAYS} дней"
    
    payment_url = payments_service.create_payment_link(
        user_id=user_id,
        channel_id=channel_id,
        amount=price,
        purpose=purpose
    )
    
    if payment_url:
        logging.info(f"✅ Создана платёжная ссылка для канала {channel_id}")
    
    return payment_url


def activate_subscription(user_id: str, channel_id: str, days: int = SUBSCRIPTION_DAYS):
    """
    Активирует подписку пользователя на канал.
    """
    expires_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db.add_subscriber(channel_id, user_id, expires_at)
    logging.info(f"✅ Подписка активирована: user={user_id}, channel={channel_id}, до {expires_at}")
    return expires_at
