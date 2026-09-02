# services/scheduler.py
import logging
import asyncio
from datetime import datetime
import database as db
import config


async def check_expired_subscriptions():
    """Проверяет истёкшие подписки и кикает подписчиков."""
    expired = db.get_expired_subscribers()
    
    if not expired:
        logging.info("Нет истёкших подписок")
        return
    
    for sub in expired:
        channel_id = sub["channel_id"]
        user_id = sub["user_id"]
        
        try:
            # Кикаем из канала
            import requests as r
            url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/banChatMember"
            response = r.post(url, json={
                "chat_id": int(channel_id),
                "user_id": int(user_id)
            })
            
            if response.status_code == 200:
                # Разбаниваем, чтобы мог вернуться
                unban_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/unbanChatMember"
                r.post(unban_url, json={
                    "chat_id": int(channel_id),
                    "user_id": int(user_id)
                })
                
                db.mark_subscriber_expired(channel_id, user_id)
                logging.info(f"✅ Пользователь {user_id} кикнут из {channel_id}")
            else:
                logging.error(f"❌ Ошибка кика {user_id}: {response.text}")
        except Exception as e:
            logging.error(f"❌ Ошибка: {e}")


async def run_scheduler():
    """Запускает периодическую проверку."""
    while True:
        await check_expired_subscriptions()
        await asyncio.sleep(3600)  # Проверка каждый час
